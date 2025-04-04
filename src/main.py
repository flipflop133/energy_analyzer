"""Energy Pipeline CLI."""

import argparse
import logging
import os
import random

import git
from tqdm.contrib.logging import logging_redirect_tqdm

from config.config_model import PipelineConfig
from config.config_store import Config
from pipeline.core_stages.build_stage import BuildStage

# Core Stages
from pipeline.core_stages.checkout_stage import CheckoutStage
from pipeline.core_stages.copy_directory_stage import CopyDirectoryStage
from pipeline.core_stages.measure_stage import MeasureEnergyStage
from pipeline.core_stages.post_test_stage import PostTestStage
from pipeline.core_stages.pre_build_stage import PreBuildStage
from pipeline.core_stages.set_directory_stage import SetDirectoryStage
from pipeline.core_stages.stability_check_stage import StabilityCheckStage
from pipeline.core_stages.temperature_check_stage import TemperatureCheckStage
from pipeline.core_stages.verify_perf_stage import VerifyPerfStage
from pipeline.custom_stages.java_setup_stage import JavaSetupStage
from pipeline.pipeline import Pipeline
from pipeline.stage_interface import PipelineStage


def load_pipeline_config(config_path: str) -> None:
    """Load the pipeline configuration from the specified file and set it as the configuration for the pipeline.

    Args:
        config_path (str): The path to the JSON configuration file.

    Returns:
        None
    """
    import json

    with open(config_path) as f:
        data = json.load(f)
    pipeline_config = PipelineConfig(**data)
    Config.set_config(pipeline_config)


def clone_or_open_repo(repo_path: str, repo_url: str, clone_options: list[str] | None = None) -> git.Repo:
    """Clone a Git repository from a URL or open an existing repository from a local path.

    Args:
        repo_path (str): The local path where the repository should be cloned or opened.
        repo_url (str): The URL of the remote Git repository.
        clone_options (list, optional): Additional options to pass to the clone command.

    Returns:
        git.Repo: An instance of the Git repository at the specified path.
    """
    if not os.path.exists(repo_path):
        logging.info("Cloning %s into %s", repo_url, repo_path)
        clone_opts = clone_options or []
        return git.Repo.clone_from(repo_url, repo_path, multi_options=clone_opts)
    else:
        logging.info("Using existing repo at %s", repo_path)
        return git.Repo(repo_path)


def gather_commits(repo: git.Repo) -> list[git.Commit]:
    """Gather the commits that should be processed according to the execution plan.

    For branches, we just take one commit per branch. For tags, we take the num_commits newest
    commits for each tag. For commits, we take the specified number of commits from the specified
    branch, starting from the newest commit. If oldest_commit is specified, we start from there.
    If newest_commit is specified, we stop at that commit.

    Args:
        repo (git.Repo): The git repository to gather commits from.

    Returns:
        list[git.Commit]: The list of commits to process.
    """
    conf = Config.get_config()
    plan = conf.execution_plan

    if plan.granularity == "branches":
        # One commit per branch
        branches = list(repo.remotes.origin.refs)
        return [branch.commit for branch in branches]

    elif plan.granularity == "tags":
        tags = list(repo.tags)
        commits = []
        for tag in tags:
            commits.extend(list(repo.iter_commits(tag, max_count=plan.num_commits)))
        return commits

    else:  # commits
        commits = list(repo.iter_commits(conf.repo.branch))
        if plan.oldest_commit:
            # cut off from oldest_commit forward
            start_idx = next((i for i, c in enumerate(commits) if c.hexsha == plan.oldest_commit), None)
            if start_idx is not None:
                commits = commits[start_idx:]
        if plan.newest_commit:
            # cut off up to newest_commit
            end_idx = next((i for i, c in enumerate(commits) if c.hexsha == plan.newest_commit), None)
            if end_idx is not None:
                commits = commits[: end_idx + 1]

        if plan.num_commits:
            commits = commits[: plan.num_commits]

        return commits


pre_stages: list[PipelineStage] = [
    VerifyPerfStage(),
    # StabilityCheckStage(),
]

pre_test_stages: list[PipelineStage] = [
    CopyDirectoryStage(),
    SetDirectoryStage(),
    CheckoutStage(),
    JavaSetupStage(),
    BuildStage(),
    # PreBuildStage(),
]

batch_stages: list[PipelineStage] = [
    TemperatureCheckStage(),
    SetDirectoryStage(),
    JavaSetupStage(),
    MeasureEnergyStage(),
    PostTestStage(),
]


def compile_stages() -> dict[str, list[PipelineStage]]:
    """Compile the pipeline stages based on the execution plan.

    Returns:
        list[PipelineStage]: The compiled list of pipeline stages.
    """
    return {"pre_stages": pre_stages, "pre_test_stages": pre_test_stages, "batch_stages": batch_stages}


def measure() -> None:
    load_pipeline_config(args.config)
    conf = Config.get_config()

    # Set up repo path
    project_name = os.path.basename(conf.repo.url).replace(".git", "").lower()
    project_dir = os.path.join("projects", project_name)
    os.makedirs(project_dir, exist_ok=True)

    repo_path: str = os.path.abspath(os.path.join(project_dir, f".cache_{project_name}"))
    repo = clone_or_open_repo(repo_path, conf.repo.url, conf.repo.clone_options)

    # (Optional) run system-level setup commands
    if conf.setup_commands:
        for cmd in conf.setup_commands:
            logging.info("Running setup command: %s", cmd)
            os.system(cmd)

    commits = gather_commits(repo)
    logging.info("Collected %d commits to process.", len(commits))

    # Divide the list of commits into batches of 'batch_size' commits each
    commit_batches = [
        commits[i : i + conf.execution_plan.batch_size] for i in range(0, len(commits), conf.execution_plan.batch_size)
    ]

    batches = []
    for commit_batch in commit_batches:
        batch_tasks = []
        for commit in commit_batch:
            # Add all the runs and repeats for this commit to the batch
            runs_per_commit = conf.execution_plan.num_runs * conf.execution_plan.num_repeats
            batch_tasks.extend([commit] * runs_per_commit)

        if conf.execution_plan.randomize_tasks:
            random.shuffle(batch_tasks)

        batches.append(batch_tasks)

    pipeline = Pipeline(compile_stages(), repo_path)
    pipeline.run(batches)

    # Finally, restore HEAD
    repo.git.checkout(conf.repo.branch)
    logging.info("Restored HEAD to latest commit on branch %s.", conf.repo.branch)


def main(args: argparse.Namespace) -> None:
    """Main entry point for the Energy Pipeline CLI.

    Parses command-line arguments to determine the command to execute
    (either 'measure' or 'stability-test') and the configuration file
    to use. Loads the pipeline configuration, sets up the repository
    path, and executes optional system-level setup commands. Gathers
    the commits to process and optionally randomizes or replicates them
    according to the execution plan. Assembles the pipeline stages and
    runs the pipeline on each commit, updating a progress bar with global
    statistics. Finally, restores the repository's HEAD to the latest
    commit on the specified branch.
    """
    # switch case for command
    match args.command:
        case "measure":
            # Measure energy consumption
            measure()
        case "stability-test":
            # Run stability test
            pass
        case "sort":
            # Sort a result file
            pass
        case "plot":
            # Plot a result file
            pass
        case _:
            raise ValueError(f"Unknown command: {args.command}")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        argparse.Namespace: Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Energy Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommand to run")

    # measure subcommand
    measure_parser = subparsers.add_parser("measure", help="Run energy measurement")
    measure_parser.add_argument("--config", default="config.json", help="Path to config file")

    # stability-test subcommand
    stability_parser = subparsers.add_parser("stability-test", help="Run stability test")
    stability_parser.add_argument("--config", default="config.json", help="Path to config file")

    # sort subcommand
    sort_parser = subparsers.add_parser("sort", help="Sort a result file")
    sort_parser.add_argument("file", help="Path to the result file to sort")

    # plot subcommand
    plot_parser = subparsers.add_parser("plot", help="Plot a result file")
    plot_parser.add_argument("file", help="Path to the result file to plot")

    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    with logging_redirect_tqdm():
        # Redirect tqdm output to logging
        main(parse_args())

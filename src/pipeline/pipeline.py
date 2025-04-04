"""Pipeline orchestrator for running stages on commits in a repository."""

import concurrent.futures
import logging
from typing import Any

import git
from tqdm import tqdm

from config.config_store import Config
from pipeline.stage_interface import PipelineStage


def run_pre_test_stages_for_commit(commit_hexsha: str, stages: list[PipelineStage], repo_path: str) -> dict[str, Any]:
    """Process the pre-test stages for a single commit in a separate process.

    Instead of receiving a git.Commit object (which might not be picklable), we pass the commit's hexsha.
    Each process reopens the repository using repo_path and retrieves the commit object.

    Args:
        commit_hexsha (str): The hexsha of the commit to process.
        stages (list[PipelineStage]): The list of pre-test stages to run.
        repo_path (str): Path to the repository.

    Returns:
        dict: The context after processing the stages.
    """
    commit_context = {"commit": str, "build_failed": False, "abort_pipeline": False, "repo_path": repo_path}
    try:
        # Re-open the repository in the worker process.
        repo = git.Repo(repo_path)
        commit = repo.commit(commit_hexsha)
        commit_context["commit"] = commit.hexsha
    except Exception:
        logging.exception(f"Failed to open repo or retrieve commit {commit_hexsha}")
        commit_context["abort_pipeline"] = True
        return commit_context

    # Execute each pre-test stage.
    for stage in stages:
        stage.run(commit_context)
        if commit_context.get("abort_pipeline"):
            break
    return commit_context


class Pipeline:
    """Orchestrates the provided stages for each commit in sequence."""

    def __init__(self, stages: dict[str, list[PipelineStage]], repo_path: str) -> None:
        """Initializes the Pipeline with the given stages and configuration.

        Args:
            stages (dict[str, list[PipelineStage]]): A dictionary where the keys are stage names (as strings)
                and the values are lists of PipelineStage objects representing the stages of the pipeline.
            repo_path (str): The path to the Git repository to be processed.

        """
        self.stages = stages
        self.config = Config.get_config()
        self.repo_path = repo_path

    def _run_stage_group(self, stages: list[PipelineStage], context: dict[str, Any]) -> bool:
        """Run a group of stages with the given context.

        Returns True if all stages ran successfully, or False if a stage requested an abort.
        """
        for stage in stages:
            stage.run(context)
            if context.get("abort_pipeline"):
                logging.warning("Aborting remaining stages for stage %s", stage.__class__.__name__)
                return False
        return True

    def run(self, batches: list[list[git.Commit]]) -> None:
        """Executes the pipeline over a list of batches, where each batch contains a list of commits.

        Args:
            batches (list[list[git.Commit]]): A list of batches, where each batch is a list of
                `git.Commit` objects to process.

        Returns:
            None

        The method processes each batch sequentially, performing the following steps:
        1. Executes pre-stages sequentially for the entire batch.
        2. Runs pre-test stages concurrently for unique commits in the batch using a
           `ProcessPoolExecutor`.
        3. Processes each commit in the batch sequentially through the pipeline stages.

        Progress is displayed using `tqdm` progress bars at various levels:
        - Overall pipeline progress across batches.
        - Pre-test stages progress for unique commits in a batch.
        - Batch stages progress for individual commits in a batch.

        Logging is used to provide detailed information about the pipeline's execution, including:
        - Batch and commit processing status.
        - Exceptions encountered during pre-test stages.
        - Warnings for aborted stages or commits.

        The pipeline can be aborted at various stages based on the context flags:
        - `build_failed`: Indicates a failure in the build process.
        - `abort_pipeline`: Signals to stop further processing.

        Raises:
            Exception: If any exception occurs during the execution of pre-test stages.
        """
        with tqdm(total=len(batches), desc="Energy Pipeline", unit="batch") as progress_bar:
            for batch in batches:
                logging.info("Processing batch of %d tasks", len(batch))

                # Run pre-stages sequentially.
                pre_context = {"build_failed": False, "abort_pipeline": False, "repo_path": self.repo_path}
                if not self._run_stage_group(self.stages.get("pre_stages", []), pre_context):
                    return

                # Prepare unique commit identifiers (hexsha strings) for the pre-test stages.
                unique_commit_hexshas = list({commit.hexsha for commit in batch})
                pre_test_stages = self.stages.get("pre_test_stages", [])

                # Run pre-test stages concurrently using ProcessPoolExecutor.
                with concurrent.futures.ProcessPoolExecutor() as executor:
                    futures = {
                        executor.submit(
                            run_pre_test_stages_for_commit,
                            commit_hexsha,
                            pre_test_stages,
                            self.repo_path,
                        ): commit_hexsha
                        for commit_hexsha in unique_commit_hexshas
                    }
                    with tqdm(total=len(futures), desc="Pre batch stages", unit="commit", leave=False) as inner_progress_bar:
                        for future in concurrent.futures.as_completed(futures):
                            commit_hexsha = futures[future]
                            try:
                                result = future.result()
                            except Exception:
                                logging.exception(f"Commit {commit_hexsha} generated an exception.")
                                return
                            inner_progress_bar.set_postfix(current_commit=commit_hexsha[:8])
                            inner_progress_bar.update(1)
                            # Optionally check the result for an abort signal:
                            if result.get("abort_pipeline"):
                                logging.warning("Aborting pre-test stages for commit %s", commit_hexsha)
                                return

                # Run pipeline stages for each commit in the batch sequentially.
                with tqdm(total=len(batch), desc="Batch stages", unit="commit", leave=False) as inner_progress_bar:
                    logging.info("Starting pipeline over %d commits...", len(batch))
                    for commit in batch:
                        inner_progress_bar.set_postfix(current_commit=commit.hexsha[:8])
                        inner_progress_bar.update(1)
                        commit_context = {
                            "commit": commit,
                            "build_failed": False,
                            "abort_pipeline": False,
                            "repo_path": self.repo_path,
                        }
                        logging.info("==== Processing commit %s ====", commit.hexsha)

                        if not self._run_stage_group(self.stages.get("batch_stages", []), commit_context):
                            logging.warning("Aborting remaining stages for commit %s", commit.hexsha)
                            return

                        logging.info("==== Done with commit %s ====\n", commit.hexsha)

                progress_bar.update(1)

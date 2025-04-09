"""Module to build the project if in 'benchmarks' mode or skip if in 'tests' mode."""

from typing import Any

from config.config_store import Config
from pipeline.stage_interface import PipelineStage
from utils.logger import logger
from utils.utils import run_command


class BuildStage(PipelineStage):
    """Builds the project if in 'benchmarks' mode, or skip if 'tests' mode has no build commands."""

    def run(self, context: dict[str, Any]) -> None:
        """Executes the build stage of the pipeline based on the provided context and configuration.

        This method handles two primary modes of operation:
        1. Benchmarks Mode: Executes a series of build commands specified in the configuration.
           If any command fails, the pipeline may be aborted based on the configuration.
        2. Batch Mode: Creates multiple copies of the repository, runs build commands in each
           copy, and handles failures similarly to benchmarks mode.

        Args:
            context (dict[str, Any]): A dictionary to store the state of the pipeline execution.
                Keys used:
                    - "build_failed" (bool): Indicates if any build command failed.
                    - "abort_pipeline" (bool): Indicates if the pipeline should be aborted.

        Raises:
            None: This method does not raise exceptions but modifies the context to signal
            failures or pipeline abortion.

        Notes:
            - The behavior of the pipeline is influenced by the configuration, specifically:
                - `config.execution_plan.mode`: Determines if the pipeline is in benchmarks mode.
                - `config.execution_plan.batch_size`: Specifies the number of repository copies
                  to create in batch mode.
                - `config.execution_plan.compile_commands`: The list of build commands to execute.
                - `config.execution_plan.ignore_failures`: If True, the pipeline will not abort
                  on build failures.
            - The repository path is derived from `config.repo_path`.
        """
        config = Config.get_config()

        compile_cmds = config.execution_plan.compile_commands or []
        for cmd in compile_cmds:
            logger.info(f"Running build command: {cmd}", context=context)
            result = run_command(cmd, context=context)
            if result.returncode != 0:
                logger.error(f"Build command failed: {cmd} (code {result.returncode})", context=context)
                context["build_failed"] = True
                if not config.execution_plan.ignore_failures:
                    context["abort_pipeline"] = True
                break

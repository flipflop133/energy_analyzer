"""Page module for generating energy consumption change reports."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import pandas as pd
from bokeh.embed import components
from bokeh.resources import CDN
from git import Repo
from jinja2 import Environment, FileSystemLoader

from energytrackr.plot.data import ChangeEvent, EnergyData, EnergyStats
from energytrackr.plot.plot import EnergyPlot
from energytrackr.utils.exceptions import CantFindFileError
from energytrackr.utils.git_utils import get_commit_details_from_git


def _default_template_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "templates")


class ReportPage:
    """A class to generate energy consumption change reports in text and HTML formats.

    Attributes:
        energy_data (EnergyData): The energy data object containing statistics and change events.
        energy_column (str): The column name in the energy data to analyze.
        template_dir (str): Directory containing HTML templates for report generation.
        git_repo_path (Optional[str]): Path to the Git repository for extracting commit details.
        timestamp (str): Timestamp of the report generation in "YYYYMMDD_HHMMSS" format.
        project_name (str): Name of the project for which the report is generated.
        summary (Dict[str, float]): Overall summary statistics for the energy data.
    """

    energy_data: EnergyData
    energy_column: str
    template_dir: str
    git_repo_path: str | None
    timestamp: str
    project_name: str
    summary: dict[str, float]

    def __init__(
        self,
        energy_data: EnergyData,
        energy_column: str,
        template_dir: str | None = None,
        git_repo_path: str | None = None,
    ) -> None:
        """Initialize the ReportPage with energy data and configuration."""
        self.energy_data = energy_data
        self.energy_column = energy_column
        self.git_repo_path = git_repo_path
        self.template_dir = template_dir or _default_template_dir()
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.project_name = "Your_Project"  # Or derive from input path

        # Compute overall summary for reporting purposes
        self.summary = self.energy_data.compute_overall_summary(self.energy_column)

    def generate_text_summary(self, output_folder: str) -> None:
        """Generates a text summary of energy consumption changes and saves it to a file.

        Args:
            output_folder (str): The folder where the text summary will be saved.

        Raises:
            CantFindFileError: If the specified output folder does not exist.
        """
        if not os.path.isdir(output_folder):
            raise CantFindFileError(output_folder)

        stats = self.energy_data.stats[self.energy_column]
        change_events = self.energy_data.change_events[self.energy_column]
        lines: list[str] = [
            f"Energy Consumption Change Summary for '{self.energy_column}'",
            f"Project: {self.project_name}",
            f"Date: {self.timestamp}",
            "=" * 80,
        ]

        if not change_events:
            lines.append("No significant energy changes detected.")
        else:
            for event in change_events:
                commit_hash: str = stats.valid_commits[event.index]
                short_hash: str = stats.short_hashes[event.index]
                direction_str: str = "Regression (Increase)" if event.direction == "increase" else "Improvement (Decrease)"
                lines.extend(
                    [
                        f"Commit: {commit_hash} (Short: {short_hash})",
                        f"Direction: {direction_str}",
                        f"Severity: {int(event.severity * 100)}%",
                        f"Cohen's d: {event.cohen_d:.2f}",
                        "-" * 80,
                    ],
                )

        filename = os.path.join(
            output_folder,
            f"{self.project_name}_{self.energy_column}_{self.timestamp}_summary.txt",
        )
        with open(filename, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logging.info("Exported text summary to %s", filename)

    def generate_html_summary(self, output_folder: str) -> None:
        """Generates an HTML summary report for energy data analysis and saves it to the specified output folder.

        Args:
            output_folder (str): The folder where the HTML report will be saved.

        Raises:
            CantFindFileError: If the specified output folder does not exist.
        """
        if not os.path.isdir(output_folder):
            raise CantFindFileError(output_folder)

        stats = self.energy_data.stats[self.energy_column]
        change_events: list[ChangeEvent] = self.energy_data.change_events[self.energy_column]
        df_median = stats.df_median

        commit_details: dict[str, dict[str, str]] = self._get_commit_details(stats.valid_commits)
        table_rows = self._build_table_rows(
            stats,
            df_median,
            change_events,
            commit_details,
        )
        oldest_date, newest_date = self._get_commit_date_range(stats.valid_commits)

        script, div = self._generate_bokeh_components(stats)

        html: str = self._render_html_report(
            script=script,
            div=div,
            table_rows=table_rows,
            date_range=(oldest_date, newest_date),
        )

        filename = os.path.join(
            output_folder,
            f"{self.project_name}_{self.energy_column}_{self.timestamp}_summary.html",
        )
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html)
        logging.info("Exported HTML summary to %s", filename)

    def _get_commit_details(self, valid_commits: list[str]) -> dict[str, dict[str, str]]:
        """Fetches commit details for a list of valid commits using a Git repository.

        Args:
            valid_commits (list[str]): List of commit hashes to fetch details for.
            get_commit_details_from_git (Callable): Function to retrieve commit details from Git.

        Returns:
            dict[str, dict[str, str]]: A dictionary mapping commit hashes to their details.
        """
        commit_details: dict[str, dict[str, str]] = {}
        if self.git_repo_path:
            try:
                repo = Repo(self.git_repo_path)
            except Exception:
                logging.exception("Failed to open Git repository at %s", self.git_repo_path)
                return commit_details
            for commit in valid_commits:
                try:
                    commit_details[commit] = get_commit_details_from_git(commit, repo)
                except Exception:
                    logging.exception("Failed to get details for commit %s", commit)
        return commit_details

    def _build_table_rows(
        self,
        stats: EnergyStats,
        df_median: pd.DataFrame,
        change_events: list[ChangeEvent],
        commit_details: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        change_lookup = {e.index: e for e in change_events}

        def _build_row(i: int, commit: str) -> dict[str, Any]:
            row_stats = df_median[df_median["commit"] == commit].iloc[0]
            details = commit_details.get(commit, {})

            if event := change_lookup.get(i):
                row_class = "increase" if event.direction == "increase" else "decrease"
                change_str = "Regression (Increase)" if event.direction == "increase" else "Improvement (Decrease)"
                severity_str = f"{int(event.severity * 100)}%"
                cohen_str = f"{event.cohen_d:.2f}"
            else:
                row_class = ""
                change_str = "None"
                severity_str = "0%"
                cohen_str = "0.00"

            files = details.get("files_modified", [])
            files_html = f"<ul>{''.join(f'<li>{f}</li>' for f in files)}</ul>" if files else "N/A"

            return {
                "row_class": row_class,
                "short_hash": stats.short_hashes[i],
                "link": details.get("commit_link", "N/A"),
                "change_str": change_str,
                "severity_str": severity_str,
                "median_val": f"{row_stats[self.energy_column]:.2f}",
                "std_val": f"{row_stats[f'{self.energy_column}_std']:.2f}",
                "normality": "Normal" if self.energy_data.normality_flags[self.energy_column][i] else "Non-normal",
                "n_val": int(row_stats["count"]),
                "cohen_str": cohen_str,
                "files": files_html,
                "message": details.get("commit_summary", "N/A"),
            }

        return [_build_row(i, commit) for i, commit in enumerate(stats.valid_commits)]

    # ...existing code...

    def _get_commit_date_range(self, valid_commits: list[str]) -> tuple[str, str]:
        oldest_date: str = "N/A"
        newest_date: str = "N/A"
        if self.git_repo_path and valid_commits:
            try:
                repo = Repo(self.git_repo_path)
            except Exception:
                logging.exception("Failed to open Git repository at %s", self.git_repo_path)
                return oldest_date, newest_date
            try:
                oldest_date = get_commit_details_from_git(valid_commits[0], repo).get("commit_date", "N/A")
            except Exception:
                logging.exception("Failed to get oldest commit date from Git.")
            try:
                newest_date = get_commit_details_from_git(valid_commits[-1], repo).get("commit_date", "N/A")
            except Exception:
                logging.exception("Failed to get newest commit date from Git.")
        return oldest_date, newest_date

    def _generate_bokeh_components(
        self,
        stats: EnergyStats,
    ) -> tuple[str, str]:
        script: str
        div: str
        script, div = components(
            EnergyPlot(energy_column=self.energy_column, stats=stats, energy_data=self.energy_data).create_figure(),
        )
        return script, div

    def _render_html_report(
        self,
        script: str,
        div: str,
        table_rows: list[dict[str, Any]],
        date_range: tuple[str, str],
    ) -> str:
        env = Environment(loader=FileSystemLoader(self.template_dir))
        template = env.get_template("report_template.html")
        html: str = template.render(
            cdn_resources=CDN.render(),
            script=script,
            div=div,
            table_rows=table_rows,
            short_hashes=self.energy_data.stats[self.energy_column].short_hashes,
            project_name=self.project_name,
            energy_column=self.energy_column,
            timestamp=self.timestamp,
            oldest_commit_date=date_range[0],
            newest_commit_date=date_range[1],
            max_inc_pct=f"{self.summary.get('max_increase', 0) * 100:.1f}%",
            max_dec_pct=f"{self.summary.get('max_decrease', 0) * 100:.1f}%",
            **self.summary,
        )
        return html

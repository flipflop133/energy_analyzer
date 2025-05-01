# src/energytrackr/plot/builtin_data_transforms/commit_stats.py
"""Commit statistics transform for energy data analysis."""

from dataclasses import dataclass
from typing import Any

import pandas as pd

from energytrackr.plot.config import get_settings
from energytrackr.plot.core.context import Context
from energytrackr.plot.core.interfaces import Configurable, Transform
from energytrackr.utils.exceptions import CommitStatsMissingOrEmptyDataFrameError


@dataclass(frozen=True)
class CommitStatsConfig:
    """Configuration for commit statistics transform."""

    column: str | None = None
    min_measurements: int | None = None


class CommitStats(Transform, Configurable[CommitStatsConfig]):
    """Compute commit statistics for a given column in the DataFrame.

    Groups the DataFrame by commit, computes median, std, count,
    filters out commits with too few measurements, and writes:
      ctx.stats["valid_commits"]  -> list of commit hashes
      ctx.stats["short_hashes"]   -> list of 7-char hashes
      ctx.stats["x_indices"]      -> [0, 1, 2, ...]
      ctx.stats["medians"]        -> list of median values
      ctx.stats["y_errors"]       -> list of std (NaN->0)
      ctx.stats["df_median"]      -> the merged DataFrame
    """

    def __init__(self, **params: dict[str, Any]) -> None:
        """Initialize the outlier filter with configuration parameters."""
        super().__init__(CommitStatsConfig, **params)
        data = get_settings().energytrackr.data
        self.column = self.config.column or data.energy_fields[0]
        self.min_measurements = self.config.min_measurements or data.min_measurements

    def apply(self, ctx: Context) -> None:
        """Apply the CommitStats transform to the context.

        Computes statistics for the specified column in the DataFrame and updates the context with the results.

        Args:
            ctx (Context): The context containing the DataFrame and other artefacts.

        Raises:
            CommitStatsMissingOrEmptyDataFrameError: If the DataFrame is missing or empty, or if the specified column
            is not found.
        """
        col = self.column
        min_meas = self.min_measurements

        df: pd.DataFrame = ctx.artefacts.get("df", pd.DataFrame())
        if df.empty or col not in df.columns:
            raise CommitStatsMissingOrEmptyDataFrameError(col)

        # Compute count, median, std
        counts = df.groupby("commit").size().rename("count")
        median = df.groupby("commit", sort=False)[col].median().rename(col)
        stddev = df.groupby("commit", sort=False)[col].std().rename(f"{col}_std")

        # Merge into one DataFrame, preserve original order if possible
        df_m = pd.concat([median, stddev, counts], axis=1).reset_index()

        # Filter out commits with too few measurements
        df_m = df_m[df_m["count"] >= min_meas].copy()

        # Build context lists
        valid_commits = df_m["commit"].tolist()
        short_hashes = [c[:7] for c in valid_commits]
        x_indices = list(range(len(valid_commits)))
        medians = df_m[col].tolist()
        y_errors = df_m[f"{col}_std"].fillna(0.0).tolist()

        # Store into ctx.stats
        ctx.stats.update({
            "valid_commits": valid_commits,
            "short_hashes": short_hashes,
            "x_indices": x_indices,
            "medians": medians,
            "y_errors": y_errors,
            "df_median": df_m,
        })

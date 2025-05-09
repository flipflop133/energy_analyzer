"""ErrorBars - vertical segments showing ±sigma around the median."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from bokeh.models import ColumnDataSource
from bokeh.plotting import figure

from energytrackr.plot.core.context import Context
from energytrackr.plot.core.interfaces import Configurable, PlotObj
from energytrackr.utils.logger import logger


@dataclass(frozen=True)
class ErrorBarsConfig:
    """Configuration for error bars in a plot.

    Attributes:
        line_width (int): Width of the error bar lines.
        color (str): Color of the error bars.
        legend (str): Label for the error bars in the plot legend.
    """

    line_width: int = 2
    color: str = "black"
    legend: str = "Error Bars"


class ErrorBars(PlotObj, Configurable[ErrorBarsConfig]):
    """A plot object for rendering error bars on a plot.

    Attributes:
        line_width (int): Width of the error bar lines.
        color (str): Color of the error bars.
        legend (str): Label for the error bars in the plot legend.

    Methods:
        add(ctx: Context, ``**params``: Any) -> None:
            Adds error bars to the given plot context using median values and their associated errors.
            The error bars are drawn as vertical segments from (x, median - error) to (x, median + error).
            Uses data from ctx.stats: "x_indices", "medians", and "y_errors".
    """

    def __init__(self, **params: dict[str, Any]) -> None:
        """Initialize the ErrorBars object with line width, color, and legend label.

        Args:
            **params: Configuration parameters for the error bars.
        """
        super().__init__(ErrorBarsConfig, **params)

    def add(self, ctx: Context, fig: figure) -> None:
        """Adds vertical error bars to the plot using the provided context.

        Retrieves x-coordinates, median y-values, and y-error values from the context's statistics.
        Computes the lower and upper bounds for the error bars, constructs a data source,
        and adds the error bars as vertical segments to the figure.

        Args:
            ctx (Context): The plotting context containing figure and statistical data.
            fig (figure): The Bokeh figure to which the error bars will be added.
        """
        x: Sequence[int] = ctx.stats["x_indices"]
        y: Sequence[float] = ctx.stats["medians"]
        err: Sequence[float] = ctx.stats["y_errors"]
        lower = [m - e for m, e in zip(y, err, strict=True)]
        upper = [m + e for m, e in zip(y, err, strict=True)]
        src = ColumnDataSource({"x": x, "y_lower": lower, "y_upper": upper})
        fig.segment(
            "x",
            "y_lower",
            "x",
            "y_upper",
            source=src,
            line_width=self.config.line_width,
            color=self.config.color,
            legend_label=self.config.legend,
        )
        logger.debug("ErrorBars added with %d segments", len(x))

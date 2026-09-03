from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data.models import MarketQuote
from utils.logger import get_logger

logger = get_logger(__name__)


def build_performer_history_image(
    histories: dict[str, Any],
    quotes: Sequence[MarketQuote],
    output_dir: Path,
    generated_at: datetime,
    group_key: str,
    line_color: str,
) -> tuple[tuple[str, str] | None, str | None]:
    available = [(quote, histories.get(quote.ticker)) for quote in quotes]
    available = [
        (quote, frame)
        for quote, frame in available
        if frame is not None and not frame.empty
    ]
    if not available:
        return None, f"No historical data was available for the {group_key} charts."

    # Chart rendering is optional: no Plotly/Kaleido failure may block the brief.
    try:
        columns = len(available)
        titles = [
            f"{quote.ticker}  {quote.percentage_change:+.2f}%"
            if quote.percentage_change is not None else quote.ticker
            for quote, _ in available
        ]
        figure = make_subplots(
            rows=1,
            cols=columns,
            subplot_titles=titles,
            horizontal_spacing=0.07,
        )
        for index, (quote, frame) in enumerate(available):
            figure.add_trace(
                go.Scatter(
                    x=frame["Date"],
                    y=frame["Price"],
                    mode="lines",
                    name=quote.ticker,
                    line={"color": line_color, "width": 2.4},
                    showlegend=False,
                ),
                row=1,
                col=index + 1,
            )
        figure.update_xaxes(
            showgrid=False, tickfont={"size": 9}, linecolor="#d8e5dd",
        )
        figure.update_yaxes(
            gridcolor="#e3ebe6", tickfont={"size": 9}, tickformat=",.2f",
        )
        figure.update_annotations(font={"size": 12, "color": "#3a7059"})
        figure.update_layout(
            width=1000,
            height=300,
            margin={"l": 45, "r": 30, "t": 45, "b": 35},
            paper_bgcolor="#ffffff",
            plot_bgcolor="#ffffff",
            font={"family": "Arial", "color": "#20352c"},
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = generated_at.strftime("%Y%m%d_%H%M%S_%f")
        path = output_dir / f"{group_key}_{stamp}.png"
        content_id = f"{group_key}-{stamp}"
        figure.write_image(str(path), format="png", scale=1.5)
        return (str(path.resolve()), content_id), None
    except Exception as exc:
        logger.exception("Could not render %s history image", group_key)
        warning = f"The {group_key} charts were omitted from the email: {str(exc)[:220]}"
        return None, warning

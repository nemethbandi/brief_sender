from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from data.models import MarketQuote
from reports.chart_builder import build_performer_history_image


def test_portfolio_history_chart_renders_png(tmp_path: Path) -> None:
    history = pd.DataFrame({
        "Date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        "Price": [100.0, 102.0, 101.0],
    })
    quote = MarketQuote("Alpha", "AAA", "stock")
    image, warning = build_performer_history_image(
        {"AAA": history}, [quote], tmp_path, datetime.now(timezone.utc),
        "top_performers", "#50b748",
    )
    assert warning is None
    assert image is not None
    assert Path(image[0]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert image[1].startswith("top_performers-")

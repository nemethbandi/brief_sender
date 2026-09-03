from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    # The application remains importable for diagnostics before requirements are installed.
    pass


@dataclass(slots=True)
class AppSettings:
    recipient: str = ""
    report_title: str = "OTP Alapkezelő Morning Brief"
    lookback_hours: int = 24
    max_top_stories: int = 8
    max_company_stories: int = 4
    include_all_portfolio_news: bool = True
    chart_period: str = "3mo"
    auto_open_outlook: bool = True
    timezone: str = "Europe/Budapest"
    market_cache_seconds: int = 180
    news_cache_seconds: int = 600
    duplicate_threshold: float = 0.80
    thresholds: dict[str, float] = field(default_factory=lambda: {
        "equity_index": 1.0, "stock": 2.0, "fx": 0.5,
        "commodity": 1.5, "volatility": 5.0, "rates": 1.0,
    })


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or BASE_DIR / "config" / "user_settings.json"
    data: dict[str, Any] = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    valid = AppSettings.__dataclass_fields__.keys()
    settings = AppSettings(**{k: v for k, v in data.items() if k in valid})
    if settings.report_title == "Morning Market Brief":
        settings.report_title = "OTP Alapkezelő Morning Brief"
    settings.recipient = os.getenv("PORTFOLIO_MANAGER_EMAIL", settings.recipient)
    return settings


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    target = path or BASE_DIR / "config" / "user_settings.json"
    target.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")

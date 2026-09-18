from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
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
    chart_period: str = "3mo"
    timezone: str = "Europe/Budapest"


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

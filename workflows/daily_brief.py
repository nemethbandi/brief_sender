from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from config.settings import BASE_DIR, AppSettings, load_settings
from data.market_data import (
    MarketDataProvider, MarketDataService, create_market_data_provider,
)
from processing import momentum as momentum_module
from reports.report_builder import Report, ReportBuilder
from storage.database import Database
from storage.momentum_database import (
    MomentumDatabase, build_demo_previous_ranking, calendar_month_before,
    calendar_months_before, compare_rankings,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def normalize_as_of(
    value: date | datetime | str, timezone_name: str,
) -> datetime:
    timezone = ZoneInfo(timezone_name)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        parsed_value = str(value)
        try:
            parsed = datetime.fromisoformat(parsed_value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.combine(date.fromisoformat(parsed_value), time.min)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def load_current_portfolio() -> list[dict[str, Any]]:
    """Current local source; replace this function with the future SQL repository."""
    configured_path = os.getenv("MARKET_BRIEF_DB_PATH")
    database = Database(Path(configured_path) if configured_path else None)
    database.seed_json(
        BASE_DIR / "config" / "portfolio.json",
        BASE_DIR / "config" / "assets.json",
    )
    rows = database.list_portfolio(True)
    if not rows:
        raise RuntimeError("The current portfolio source returned no enabled tickers")
    logger.info("Loaded %d enabled portfolio rows from the current local source", len(rows))
    return rows


def portfolio_assets(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for row in rows:
        asset = {
            "name": row.get("name") or row["ticker"],
            "ticker": str(row["ticker"]),
            "asset_class": "stock",
            "region": "Portfolio",
            "format": "currency",
            "threshold_pct": float(row.get("threshold_pct") or 2.0),
        }
        provider_id = row.get("data_id") or row.get("ric")
        if provider_id:
            asset["data_id"] = str(provider_id)
        assets.append(asset)
    return assets


def _enrich_metadata(
    frame: pd.DataFrame, metadata: dict[str, dict[str, str]],
) -> pd.DataFrame:
    enriched = frame.copy()
    if "sector" not in enriched.columns:
        enriched["sector"] = None
    if "industry" not in enriched.columns:
        enriched["industry"] = None
    for index, row in enriched.iterrows():
        details = metadata.get(str(row["ticker"]), {})
        if pd.isna(row.get("sector")) or not row.get("sector"):
            enriched.at[index, "sector"] = details.get("sector", "Unknown")
        if pd.isna(row.get("industry")) or not row.get("industry"):
            enriched.at[index, "industry"] = details.get("industry", "Unknown")
    return enriched


def build_momentum_context(
    as_of_date: date,
    momentum_database: MomentumDatabase | None = None,
    data_provider: MarketDataProvider | None = None,
) -> dict[str, Any]:
    database = momentum_database or MomentumDatabase()
    provider = data_provider or create_market_data_provider()
    universe = provider.get_momentum_universe()
    prices = provider.get_momentum_prices(universe)
    ranking = momentum_module.calculate_momentum(prices)
    if ranking.empty:
        raise RuntimeError("No securities produced a valid momentum score")

    one_month_target = calendar_month_before(as_of_date)
    previous = database.get_latest_ranking_on_or_before(one_month_target)
    one_month_demo = previous.empty
    if one_month_demo:
        previous = build_demo_previous_ranking(ranking)
        one_month_date = one_month_target.isoformat()
    else:
        one_month_date = str(previous.iloc[0]["as_of_date"])

    three_month_target = calendar_months_before(as_of_date, 3)
    three_month = database.get_latest_ranking_on_or_before(three_month_target)
    three_month_demo = three_month.empty
    if three_month_demo:
        three_month = build_demo_previous_ranking(ranking, replacement_count=4)
        three_month_date = three_month_target.isoformat()
    else:
        three_month_date = str(three_month.iloc[0]["as_of_date"])

    metadata_tickers = sorted(set(
        ranking.sort_values("rank").head(25)["ticker"].tolist()
        + previous.sort_values("rank").head(25)["ticker"].tolist()
        + three_month.sort_values("rank").head(25)["ticker"].tolist()
    ))
    stale_tickers = database.metadata_tickers_to_refresh(metadata_tickers)
    if stale_tickers:
        fetched = provider.get_security_metadata(stale_tickers)
        resolved = [
            row for row in fetched
            if row["sector"] != "Unknown" or row["industry"] != "Unknown"
        ]
        database.upsert_security_metadata(resolved)
    metadata = database.get_security_metadata(metadata_tickers)
    ranking = _enrich_metadata(ranking, metadata)
    previous = _enrich_metadata(previous, metadata)
    three_month = _enrich_metadata(three_month, metadata)

    # The full current ranking is persisted before any email delivery attempt.
    rows_written = database.upsert_rankings(as_of_date, ranking)
    changes = compare_rankings(ranking, previous, n=25)
    sectors = momentum_module.compare_sector_distribution(
        ranking, previous, three_month, n=25,
    )
    logger.info(
        "Momentum context complete for %s: universe=%d ranking=%d persisted=%d",
        as_of_date, len(universe), len(ranking), rows_written,
    )
    return {
        "ranking": ranking,
        "changes": changes,
        "sector_comparison": sectors,
        "one_month_date": one_month_date,
        "one_month_demo": one_month_demo,
        "three_month_date": three_month_date,
        "three_month_demo": three_month_demo,
        "rows_written": rows_written,
        "universe_size": len(universe),
    }


def build_report(
    as_of: datetime,
    settings: AppSettings,
    portfolio_rows: Sequence[dict[str, Any]],
    market_service: MarketDataService | None = None,
    momentum_database: MomentumDatabase | None = None,
) -> tuple[Report, dict[str, Any]]:
    service = market_service or MarketDataService()
    assets = portfolio_assets(portfolio_rows)
    quotes = service.get_quotes(assets)
    chart_period = settings.chart_period
    histories = service.get_histories(assets, chart_period)
    stop_histories = (
        histories if chart_period == "6mo" else service.get_histories(assets, "6mo")
    )
    notable = MarketDataService.notable_portfolio_moves(quotes)

    momentum_warning = None
    try:
        momentum = build_momentum_context(
            as_of.date(), momentum_database, service.provider,
        )
    except Exception as exc:
        logger.exception("Momentum calculation failed without blocking the portfolio brief")
        momentum_warning = f"Momentum ranking unavailable: {str(exc)[:240]}"
        momentum = {
            "ranking": pd.DataFrame(), "changes": None, "sector_comparison": None,
            "one_month_date": None, "one_month_demo": False,
            "three_month_date": None, "three_month_demo": False,
            "rows_written": 0, "universe_size": 0,
        }

    ranking_records = momentum["ranking"].to_dict("records")
    report = ReportBuilder(timezone_name=settings.timezone).build(
        settings.report_title,
        notable,
        as_of,
        portfolio_quotes=quotes,
        portfolio_history=histories,
        stop_loss_history=stop_histories,
        momentum_top25=ranking_records[:25],
        momentum_changes=momentum["changes"],
        momentum_sector_comparison=momentum["sector_comparison"],
        momentum_sector_3m_date=momentum["three_month_date"],
        momentum_sector_3m_demo=momentum["three_month_demo"],
        momentum_comparison_date=momentum["one_month_date"],
        momentum_comparison_demo=momentum["one_month_demo"],
        generated_at=as_of,
    )
    if momentum_warning:
        report.chart_warning = " ".join(filter(None, [report.chart_warning, momentum_warning]))
    return report, momentum


def prepare_email_payload(report: Report) -> tuple[str, list[str]]:
    """Translate report CIDs to send_email.py's positional inline_image_N IDs."""
    html = report.html
    image_paths: list[str] = []
    for index, (image_path, content_id) in enumerate(report.inline_images, start=1):
        html = html.replace(f"cid:{content_id}", f"cid:inline_image_{index}")
        image_paths.append(image_path)
    return html, image_paths


def deliver_report(
    report: Report,
    to_address: list[str],
    cc_address: list[str] | None = None,
    bcc_address: list[str] | None = None,
    email_function: Callable[..., str] | None = None,
) -> str:
    if not to_address:
        raise ValueError("At least one To recipient is required")
    if email_function is None:
        # This is the existing Airflow utility. SMTP/MIME logic stays there.
        from send_email import _send_email
        email_function = _send_email
    html, inline_image_paths = prepare_email_payload(report)
    result = email_function(
        to_address=to_address,
        subject=report.subject,
        body=html,
        cc_address=cc_address or None,
        bcc_address=bcc_address or None,
        inline_image_paths=inline_image_paths or None,
    )
    if result != "Email sent successfully!":
        raise RuntimeError("Existing send_email utility reported a delivery failure")
    return result


def run_daily_brief(
    as_of: date | datetime | str,
    to_address: list[str] | None = None,
    cc_address: list[str] | None = None,
    bcc_address: list[str] | None = None,
    portfolio_rows: Sequence[dict[str, Any]] | None = None,
    data_provider: MarketDataProvider | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    logical_datetime = normalize_as_of(as_of, settings.timezone)
    recipients = to_address or ([settings.recipient] if settings.recipient else [])
    selected_provider = os.getenv("MARKET_DATA_PROVIDER", "yahoo").strip().lower()
    if (
        data_provider is None
        and selected_provider in {"internal", "internal_dataframe", "dataframe"}
    ):
        from data.internal_data_source import build_internal_data_provider
        internal_rows, data_provider = build_internal_data_provider(logical_datetime)
        if portfolio_rows is None:
            portfolio_rows = internal_rows
    rows = list(portfolio_rows) if portfolio_rows is not None else load_current_portfolio()
    market_service = MarketDataService(data_provider) if data_provider else None
    report, momentum = build_report(
        logical_datetime, settings, rows, market_service=market_service,
    )
    email_result = deliver_report(report, recipients, cc_address, bcc_address)
    logger.info(
        "Daily brief delivered for logical date %s to %d recipients",
        logical_datetime.date(), len(recipients),
    )
    return {
        "as_of_date": logical_datetime.date().isoformat(),
        "subject": report.subject,
        "portfolio_size": len(rows),
        "momentum_rows": len(momentum["ranking"]),
        "momentum_rows_written": momentum["rows_written"],
        "email_result": email_result,
    }

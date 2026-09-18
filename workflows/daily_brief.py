from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from config.settings import AppSettings, load_settings
from data.market_data import (
    MarketDataProvider, MarketDataService,
)
from processing import momentum as momentum_module
from reports.report_builder import Report, ReportBuilder
from storage.momentum_database import (
    MomentumDatabase, calendar_month_before,
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
    data_provider: MarketDataProvider,
    momentum_database: MomentumDatabase | None = None,
) -> dict[str, Any]:
    database = momentum_database or MomentumDatabase()
    provider = data_provider
    universe = provider.get_momentum_universe()
    prices = provider.get_momentum_prices(universe)
    ranking = momentum_module.calculate_momentum(prices)
    if ranking.empty:
        raise RuntimeError("No securities produced a valid momentum score")

    one_month_target = calendar_month_before(as_of_date)
    previous = database.get_latest_ranking_on_or_before(one_month_target)
    one_month_date = None if previous.empty else str(previous.iloc[0]["as_of_date"])
    three_month_target = calendar_months_before(as_of_date, 3)
    three_month = database.get_latest_ranking_on_or_before(three_month_target)
    three_month_date = None if three_month.empty else str(three_month.iloc[0]["as_of_date"])

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
    changes = None if previous.empty else compare_rankings(ranking, previous, n=25)
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

        "three_month_date": three_month_date,

        "rows_written": rows_written,
        "universe_size": len(universe),
    }


@dataclass
class BriefData:
    as_of: datetime
    portfolio_rows: list[dict[str, Any]]
    quotes: list
    histories: dict
    stop_histories: dict
    momentum: dict[str, Any]
    momentum_warning: str | None = None


def load_brief_data(
    as_of: date | datetime | str,
    settings: AppSettings,
    momentum_database: MomentumDatabase | None = None,
) -> BriefData:
    """Load the same internal LSEG snapshot for Streamlit and Airflow."""
    from data.internal_data_source import build_internal_data_provider

    logical_datetime = normalize_as_of(as_of, settings.timezone)
    rows, provider = build_internal_data_provider(logical_datetime)
    service = MarketDataService(provider)
    assets = portfolio_assets(rows)
    quotes = service.get_quotes(assets)
    histories = service.get_histories(assets, settings.chart_period)
    stop_histories = (
        histories if settings.chart_period == "6mo" else service.get_histories(assets, "6mo")
    )
    warning = None
    try:
        momentum = build_momentum_context(logical_datetime.date(), provider, momentum_database)
    except Exception as exc:
        logger.exception("Momentum calculation failed without blocking the portfolio brief")
        warning = f"Momentum ranking unavailable: {str(exc)[:240]}"
        momentum = {
            "ranking": pd.DataFrame(), "changes": None, "sector_comparison": None,
            "one_month_date": None, "three_month_date": None,
            "rows_written": 0, "universe_size": 0,
        }
    return BriefData(logical_datetime, rows, quotes, histories, stop_histories, momentum, warning)


def build_report(data: BriefData, settings: AppSettings) -> Report:
    momentum = data.momentum
    quotes, histories, stop_histories = data.quotes, data.histories, data.stop_histories
    as_of = data.as_of
    notable = MarketDataService.notable_portfolio_moves(quotes)
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
        momentum_comparison_date=momentum["one_month_date"],
        generated_at=as_of,
    )
    if data.momentum_warning:
        report.chart_warning = " ".join(filter(None, [report.chart_warning, data.momentum_warning]))
    return report


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
) -> dict[str, Any]:
    settings = load_settings()
    logical_datetime = normalize_as_of(as_of, settings.timezone)
    recipients = to_address or ([settings.recipient] if settings.recipient else [])
    data = load_brief_data(logical_datetime, settings)
    report = build_report(data, settings)
    rows, momentum = data.portfolio_rows, data.momentum
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

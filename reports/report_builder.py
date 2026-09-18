from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config.settings import BASE_DIR
from data.models import MarketQuote
from processing.market_analyzer import (
    format_change, format_price, select_portfolio_performers, select_stop_loss_signals,
)
from reports.chart_builder import build_performer_history_image


@dataclass(slots=True)
class Report:
    subject: str
    html: str
    generated_at: datetime
    article_count: int
    inline_images: list[tuple[str, str]]
    chart_warning: str | None = None


class ReportBuilder:
    def __init__(self, template_dir: Path | None = None, timezone_name: str = "Europe/Budapest") -> None:
        directory = template_dir or BASE_DIR / "reports" / "templates"
        self.environment = Environment(loader=FileSystemLoader(directory), autoescape=select_autoescape(["html", "xml"]))
        self.timezone_name = timezone_name

    def build(self, title: str, notable: Sequence[MarketQuote],
              market_timestamp: datetime | None = None,
              portfolio_quotes: Sequence[MarketQuote] | None = None,
              portfolio_history: dict | None = None,
              stop_loss_history: dict | None = None,
              momentum_top25: Sequence[dict] | None = None,
              momentum_changes: dict | None = None,
              momentum_sector_comparison: Sequence[dict] | None = None,
              momentum_sector_3m_date: str | None = None,
              momentum_comparison_date: str | None = None,
              generated_at: datetime | None = None) -> Report:
        now = generated_at or datetime.now(ZoneInfo(self.timezone_name))
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo(self.timezone_name))
        else:
            now = now.astimezone(ZoneInfo(self.timezone_name))
        notable_rows = [{"ticker": q.ticker, "name": q.name, "last": format_price(q),
                         "change": format_change(q), "threshold": f"±{getattr(q, 'threshold_pct', None) or 2.0:.1f}%",
                         "marker": "▲" if (q.percentage_change or 0) > 0 else "▼" if (q.percentage_change or 0) < 0 else "•",
                         "direction": "positive" if (q.percentage_change or 0) > 0 else "negative"} for q in notable]
        portfolio_quotes = portfolio_quotes or []
        top_performers, worst_performers = select_portfolio_performers(list(portfolio_quotes))
        stop_loss_signals = select_stop_loss_signals(
            list(portfolio_quotes), stop_loss_history or {},
        )
        def stop_loss_row(signal) -> dict:
            return {
                "ticker": signal.quote.ticker,
                "name": signal.quote.name,
                "last": f"${signal.current_price:,.2f}",
                "peak": f"${signal.peak_price:,.2f}",
                "peak_date": signal.peak_date.strftime("%Y-%m-%d"),
                "drawdown": f"-{signal.drawdown_pct:.2f}%",
            }
        def performer_row(q: MarketQuote) -> dict:
            return {
                "ticker": q.ticker, "name": q.name, "last": format_price(q), "change": format_change(q),
                "marker": "▲" if (q.percentage_change or 0) > 0 else "▼" if (q.percentage_change or 0) < 0 else "•",
                "direction": "positive" if (q.percentage_change or 0) > 0 else "negative" if (q.percentage_change or 0) < 0 else "neutral",
            }
        portfolio_rows = [{
            "ticker": q.ticker, "name": q.name, "last": format_price(q), "change": format_change(q),
            "threshold": f"±{getattr(q, 'threshold_pct', None) or 2.0:.1f}%",
            "marker": "▲" if (q.percentage_change or 0) > 0 else "▼" if (q.percentage_change or 0) < 0 else "•",
            "status": "Available" if q.error is None else "N/A",
            "direction": "positive" if (q.percentage_change or 0) > 0 else "negative" if (q.percentage_change or 0) < 0 else "neutral",
        } for q in portfolio_quotes]
        portfolio_summary = {
            "positions": len(portfolio_rows),
            "up": sum(1 for q in portfolio_quotes if q.percentage_change is not None and q.percentage_change > 0),
            "down": sum(1 for q in portfolio_quotes if q.percentage_change is not None and q.percentage_change < 0),
            "unchanged": sum(1 for q in portfolio_quotes if q.percentage_change == 0),
            "unavailable": sum(1 for q in portfolio_quotes if q.percentage_change is None),
        }
        momentum_rows = [{
            "rank": int(row["rank"]),
            "ticker": str(row["ticker"]),
            "return_6m": f"{float(row['return_6m']):+.1%}",
            "return_12m": f"{float(row['return_12m']):+.1%}",
            "score_6m": f"{float(row['score_6m']):+.2f}",
            "score_12m": f"{float(row['score_12m']):+.2f}",
            "momentum_score": f"{float(row['momentum_score']):+.2f}",
        } for row in (momentum_top25 or [])]
        sector_rows = [{
            "sector": str(row["sector"]),
            "current_width": f"{float(row['current_pct']):.2f}%",
            "previous_width": f"{float(row['previous_pct']):.2f}%",
            "three_month_width": f"{float(row['three_month_pct']):.2f}%",
            "current_value": float(row["current_pct"]),
            "previous_value": float(row["previous_pct"]),
            "three_month_value": float(row["three_month_pct"]),
            "current_pct": f"{float(row['current_pct']):.1f}%",
            "previous_pct": f"{float(row['previous_pct']):.1f}%",
            "three_month_pct": f"{float(row['three_month_pct']):.1f}%",
            "change_pp": f"{float(row['change_pp']):+.1f} pp",
            "change_3m_pp": f"{float(row['change_3m_pp']):+.1f} pp",
            "change_value": float(row["change_pp"]),
            "change_3m_value": float(row["change_3m_pp"]),
            "color": str(row["color"]),
        } for row in (momentum_sector_comparison or [])]
        inline_images: list[tuple[str, str]] = []
        chart_warnings: list[str] = []
        top_chart_cid = None
        worst_chart_cid = None
        if portfolio_history is not None:
            top_chart, top_warning = build_performer_history_image(
                portfolio_history, top_performers, BASE_DIR / "storage" / "charts", now,
                "top_performers", "#50b748",
            )
            worst_chart, worst_warning = build_performer_history_image(
                portfolio_history, worst_performers, BASE_DIR / "storage" / "charts", now,
                "worst_performers", "#b3261e",
            )
            for chart_image in (top_chart, worst_chart):
                if chart_image:
                    inline_images.append(chart_image)
            top_chart_cid = top_chart[1] if top_chart else None
            worst_chart_cid = worst_chart[1] if worst_chart else None
            chart_warnings.extend(x for x in (top_warning, worst_warning) if x)
        context = {
            "title": title, "date": now.strftime("%A, %d %B %Y"), "generated": now.strftime("%H:%M %Z"),
            "notable": notable_rows,
            "stop_candidates": [
                stop_loss_row(signal) for signal in stop_loss_signals if signal.status == "STOP"
            ],
            "watch_candidates": [
                stop_loss_row(signal) for signal in stop_loss_signals if signal.status == "WATCH"
            ],
            "top_performers": [performer_row(q) for q in top_performers],
            "worst_performers": [performer_row(q) for q in worst_performers],
            "portfolio_status": portfolio_rows, "portfolio_summary": portfolio_summary,
            "momentum_top25": momentum_rows,
            "momentum_changes": momentum_changes,
            "momentum_sector_comparison": sector_rows,
            "momentum_sector_3m_date": momentum_sector_3m_date,
            "momentum_comparison_date": momentum_comparison_date,
            "top_chart_cid": top_chart_cid,
            "worst_chart_cid": worst_chart_cid,
            "market_timestamp": self._format_timestamp(market_timestamp),
        }
        html = self.environment.get_template("morning_brief.html").render(**context)
        count = 0
        chart_warning = " ".join(chart_warnings) or None
        return Report(f"{title} | {now:%Y-%m-%d}", html, now, count, inline_images, chart_warning)

    def _format_timestamp(self, value: datetime | None) -> str:
        return value.astimezone(ZoneInfo(self.timezone_name)).strftime("%H:%M %Z") if value else "N/A"

from __future__ import annotations

import base64
import json
import importlib
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import settings as settings_module
importlib.reload(settings_module)
BASE_DIR = settings_module.BASE_DIR
AppSettings = settings_module.AppSettings
load_settings = settings_module.load_settings
save_settings = settings_module.save_settings
from data import market_data as market_data_module
importlib.reload(market_data_module)
MarketDataService = market_data_module.MarketDataService
from mail import outlook_sender as outlook_sender_module
importlib.reload(outlook_sender_module)
OutlookError = outlook_sender_module.OutlookError
OutlookSender = outlook_sender_module.OutlookSender
from processing.market_analyzer import (
    format_change, format_price, select_portfolio_performers, select_stop_loss_signals,
)
from processing import momentum as momentum_module
importlib.reload(momentum_module)
from reports import report_builder as report_builder_module
from storage import database as database_module
importlib.reload(database_module)
Database = database_module.Database
from storage import momentum_database as momentum_database_module
importlib.reload(momentum_database_module)
MomentumDatabase = momentum_database_module.MomentumDatabase
from utils.helpers import normalize_ticker, utc_now
from utils.logger import get_logger

logger = get_logger(__name__)
st.set_page_config(page_title="OTP Alapkezelő Morning Brief", page_icon=None, layout="wide")
st.markdown("""<style>
.stApp{background:#f3f7f4}.block-container{max-width:1450px;padding-top:4.5rem;padding-bottom:2rem}.brief-title{font-size:2rem;font-weight:700;color:#3a7059;letter-spacing:-.02em;line-height:1.2}.brief-sub{color:#557466;font-size:.85rem;margin-top:.7rem}.story{background:#fff;border:1px solid #d8e5dd;border-left:4px solid #50b748;padding:12px 15px;margin:8px 0}.story-meta{font-size:.75rem;color:#61766c;margin-top:5px}[data-testid="stMetric"]{background:#fff;border:1px solid #d8e5dd;border-top:3px solid #50b748;padding:12px;border-radius:3px}.status-card{background:#fff;border:1px solid #d8e5dd;border-top:3px solid #3a7059;border-radius:4px;padding:11px 14px}.status-card span{display:block;font-size:11px;font-weight:700;letter-spacing:.6px;color:#61766c}.status-card strong{display:block;font-size:24px;margin-top:3px;color:#20352c}.status-card.positive{background:#edf8ec;border-color:#50b748}.status-card.positive span,.status-card.positive strong{color:#3a7059}.status-card.negative{background:#fdeceb;border-color:#d95c54}.status-card.negative span,.status-card.negative strong{color:#a72b24}.stButton>button[kind="primary"]{background:#50b748;border-color:#50b748;color:#fff}.stButton>button[kind="primary"]:hover{background:#3a7059;border-color:#3a7059}.stButton>button:focus{border-color:#50b748;box-shadow:0 0 0 .2rem rgba(80,183,72,.2)}[data-testid="stSidebar"]{background:#eef5f0}a{color:#3a7059}</style>""", unsafe_allow_html=True)


def display_change(quote) -> str:
    value = format_change(quote)
    if quote.percentage_change is None:
        return value
    if quote.percentage_change > 0:
        return f"▲ {value}"
    if quote.percentage_change < 0:
        return f"▼ {value}"
    return f"• {value}"


def browser_preview_html(report) -> str:
    """Replace email-only CID references for the Streamlit browser preview."""
    html = report.html
    for image_path, content_id in getattr(report, "inline_images", []):
        try:
            encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
            html = html.replace(
                f"cid:{content_id}", f"data:image/png;base64,{encoded}",
            )
        except OSError:
            logger.exception("Could not load preview image: %s", image_path)
    return html


def style_movement_table(frame: pd.DataFrame, column: str) -> pd.io.formats.style.Styler:
    def movement_css(value: object) -> str:
        text = str(value)
        if text.startswith("▲"):
            return "color:#3a7059;background-color:#edf8ec;font-weight:700"
        if text.startswith("▼"):
            return "color:#b3261e;background-color:#fdeceb;font-weight:700"
        return "color:#596579;font-weight:600"
    return frame.style.map(movement_css, subset=[column]) if column in frame.columns else frame.style


@st.cache_resource
def database() -> Database:
    db = Database()
    db.seed_json(BASE_DIR / "config" / "portfolio.json", BASE_DIR / "config" / "assets.json")
    return db


@st.cache_resource
def momentum_database() -> MomentumDatabase:
    return MomentumDatabase()


@st.cache_data(ttl=180, show_spinner=False)
def load_market_data(assets_json: str):
    return MarketDataService().get_quotes(json.loads(assets_json))


@st.cache_data(ttl=300, show_spinner=False)
def load_portfolio_history(assets_json: str, period: str):
    return MarketDataService().get_histories(json.loads(assets_json), period)


@st.cache_data(ttl=3600, show_spinner=False)
def load_momentum_ranking(as_of_date: str):
    provider = market_data_module.create_market_data_provider()
    universe = provider.get_momentum_universe()
    prices = provider.get_momentum_prices(universe)
    ranking = momentum_module.calculate_momentum(prices)
    if ranking.empty:
        raise ValueError("No securities produced a valid momentum score")
    logger.info("Momentum as-of date: %s", as_of_date)
    return ranking.to_dict("records"), len(universe)


def refresh_data(force: bool = False) -> None:
    db, settings = database(), st.session_state.settings
    if force:
        load_market_data.clear(); load_portfolio_history.clear(); load_momentum_ranking.clear()
    portfolio = db.list_portfolio(True)
    portfolio_assets = [
        {"name": p["name"] or p["ticker"], "ticker": p["ticker"],
         "asset_class": "stock", "region": "Portfolio", "format": "currency",
         "threshold_pct": p["threshold_pct"]}
        for p in portfolio
    ]
    with st.spinner("Retrieving portfolio and momentum market data..."):
        st.session_state.quotes = []
        st.session_state.portfolio_quotes = load_market_data(json.dumps(portfolio_assets, sort_keys=True))
        assets_json = json.dumps(portfolio_assets, sort_keys=True)
        chart_period = getattr(settings, "chart_period", "3mo")
        st.session_state.portfolio_history = load_portfolio_history(assets_json, chart_period)
        st.session_state.portfolio_stop_history = (
            st.session_state.portfolio_history
            if chart_period == "6mo" else load_portfolio_history(assets_json, "6mo")
        )
        as_of_date = datetime.now(ZoneInfo(settings.timezone)).date()
        try:
            momentum_rows, universe_size = load_momentum_ranking(as_of_date.isoformat())
            ranking = pd.DataFrame(momentum_rows, columns=momentum_module.RANKING_COLUMNS)
            momentum_db = momentum_database()
            target_date = momentum_database_module.calendar_month_before(as_of_date)
            previous_ranking = momentum_db.get_latest_ranking_on_or_before(target_date)
            comparison_is_demo = previous_ranking.empty
            if comparison_is_demo:
                previous_ranking = momentum_database_module.build_demo_previous_ranking(ranking)
                comparison_date = target_date.isoformat()
            else:
                comparison_date = str(previous_ranking.iloc[0]["as_of_date"])
            three_month_target = momentum_database_module.calendar_months_before(as_of_date, 3)
            three_month_ranking = momentum_db.get_latest_ranking_on_or_before(three_month_target)
            three_month_is_demo = three_month_ranking.empty
            if three_month_is_demo:
                three_month_ranking = momentum_database_module.build_demo_previous_ranking(
                    ranking, replacement_count=4,
                )
                three_month_date = three_month_target.isoformat()
            else:
                three_month_date = str(three_month_ranking.iloc[0]["as_of_date"])
            metadata_tickers = sorted(set(
                ranking.sort_values("rank").head(25)["ticker"].tolist()
                + previous_ranking.sort_values("rank").head(25)["ticker"].tolist()
                + three_month_ranking.sort_values("rank").head(25)["ticker"].tolist()
            ))
            stale_tickers = momentum_db.metadata_tickers_to_refresh(metadata_tickers)
            if stale_tickers:
                provider = market_data_module.create_market_data_provider()
                metadata_rows = provider.get_security_metadata(stale_tickers)
                resolved_rows = [
                    row for row in metadata_rows
                    if row["sector"] != "Unknown" or row["industry"] != "Unknown"
                ]
                momentum_db.upsert_security_metadata(resolved_rows)
            metadata = momentum_db.get_security_metadata(metadata_tickers)

            def enrich_metadata(frame: pd.DataFrame) -> pd.DataFrame:
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

            ranking = enrich_metadata(ranking)
            previous_ranking = enrich_metadata(previous_ranking)
            three_month_ranking = enrich_metadata(three_month_ranking)
            written = momentum_db.upsert_rankings(as_of_date, ranking)
            comparison = momentum_database_module.compare_rankings(
                ranking, previous_ranking, n=25,
            )
            sector_comparison = momentum_module.compare_sector_distribution(
                ranking, previous_ranking, three_month_ranking, n=25,
            )
            momentum_rows = ranking.to_dict("records")
            st.session_state.momentum_ranking = momentum_rows
            st.session_state.momentum_as_of = as_of_date.isoformat()
            st.session_state.momentum_comparison = comparison
            st.session_state.momentum_comparison_date = comparison_date
            st.session_state.momentum_comparison_demo = comparison_is_demo
            st.session_state.momentum_sector_comparison = sector_comparison
            st.session_state.momentum_sector_3m_date = three_month_date
            st.session_state.momentum_sector_3m_demo = three_month_is_demo
            st.session_state.momentum_warning = None
            logger.info(
                "Momentum daily run complete: universe=%d, ranking=%d, persisted=%d",
                universe_size, len(ranking), written,
            )
        except Exception as exc:
            logger.exception("Momentum daily run failed without blocking the portfolio brief")
            st.session_state.momentum_ranking = []
            st.session_state.momentum_as_of = as_of_date.isoformat()
            st.session_state.momentum_comparison = None
            st.session_state.momentum_comparison_date = None
            st.session_state.momentum_comparison_demo = False
            st.session_state.momentum_sector_comparison = None
            st.session_state.momentum_sector_3m_date = None
            st.session_state.momentum_sector_3m_demo = False
            st.session_state.momentum_warning = f"Momentum ranking unavailable: {str(exc)[:240]}"
    st.session_state.refreshed_at = utc_now()
    st.session_state.report = None
    logger.info("Portfolio refresh completed: %d position quotes",
                len(st.session_state.portfolio_quotes))


def generate_report() -> None:
    settings = st.session_state.settings
    stock_quotes = st.session_state.portfolio_quotes
    notable = MarketDataService.notable_portfolio_moves(stock_quotes)
    timestamp = st.session_state.refreshed_at
    # Streamlit reruns app.py without always reloading imported local modules.
    # Reloading this small, deterministic module prevents stale signatures
    # during local development and after an in-place application update.
    importlib.reload(report_builder_module)
    st.session_state.report = report_builder_module.ReportBuilder(timezone_name=settings.timezone).build(
        settings.report_title, notable, timestamp, portfolio_quotes=stock_quotes,
        portfolio_history=st.session_state.portfolio_history,
        stop_loss_history=st.session_state.portfolio_stop_history,
        momentum_top25=st.session_state.momentum_ranking[:25],
        momentum_changes=st.session_state.momentum_comparison,
        momentum_sector_comparison=st.session_state.momentum_sector_comparison,
        momentum_sector_3m_date=st.session_state.momentum_sector_3m_date,
        momentum_sector_3m_demo=st.session_state.momentum_sector_3m_demo,
        momentum_comparison_date=st.session_state.momentum_comparison_date,
        momentum_comparison_demo=st.session_state.momentum_comparison_demo)
    logger.info("Generated report with %d article references", st.session_state.report.article_count)


def sidebar() -> None:
    db, settings = database(), st.session_state.settings
    with st.sidebar:
        st.header("Portfolio")
        portfolio_df = pd.DataFrame(db.list_portfolio())[["enabled", "ticker", "name", "threshold_pct"]]
        edited = st.data_editor(portfolio_df, hide_index=True, num_rows="dynamic", use_container_width=True,
                                column_config={
                                    "enabled": st.column_config.CheckboxColumn("On"),
                                    "ticker": st.column_config.TextColumn("Ticker", required=True),
                                    "threshold_pct": st.column_config.NumberColumn(
                                        "Notable threshold (%)", min_value=0.1, max_value=100.0,
                                        step=0.1, format="%.1f", required=True,
                                        help="Absolute daily move required for this position to become notable.",
                                    ),
                                })
        if st.button("Save portfolio", use_container_width=True):
            try:
                rows = []
                for row in edited.to_dict("records"):
                    if not str(row.get("ticker", "")).strip():
                        continue
                    threshold = float(row.get("threshold_pct") or 2.0)
                    if not math.isfinite(threshold) or not 0.1 <= threshold <= 100.0:
                        raise ValueError("Each notable threshold must be between 0.1% and 100.0%.")
                    rows.append({"enabled": bool(row["enabled"]), "ticker": normalize_ticker(str(row["ticker"])),
                                 "name": str(row.get("name", "")).strip(), "threshold_pct": threshold})
                db.replace_portfolio(rows)
                st.cache_data.clear()
                st.session_state.portfolio_quotes = []
                st.session_state.portfolio_history = {}
                st.session_state.portfolio_stop_history = {}
                st.session_state.momentum_ranking = []
                st.session_state.momentum_comparison = None
                st.session_state.momentum_sector_comparison = None
                st.session_state.report = None
                st.session_state.refreshed_at = None
                st.success("Portfolio saved. Refresh Data is required before generating a new brief.")
            except (ValueError, TypeError, KeyError) as exc: st.error(str(exc))
        with st.expander("Settings"):
            recipient = st.text_input("Portfolio Manager email", settings.recipient)
            title = st.text_input("Report title", settings.report_title)
            periods = ["1mo", "3mo", "6mo", "1y", "2y"]
            saved_period = getattr(settings, "chart_period", "3mo")
            chart_period = st.selectbox("Historical chart period", periods,
                                        index=periods.index(saved_period) if saved_period in periods else 1)
            auto_open = st.checkbox("Default to Open in Outlook", settings.auto_open_outlook)
            if st.button("Save settings", use_container_width=True):
                st.session_state.settings = AppSettings(recipient=recipient.strip(), report_title=title.strip() or "OTP Alapkezelő Morning Brief",
                    chart_period=chart_period,
                    auto_open_outlook=auto_open, timezone=settings.timezone, thresholds=settings.thresholds)
                save_settings(st.session_state.settings); st.success("Settings saved.")


def main() -> None:
    if "settings" not in st.session_state: st.session_state.settings = load_settings()
    if st.session_state.settings.report_title == "Morning Market Brief":
        st.session_state.settings.report_title = "OTP Alapkezelő Morning Brief"
        save_settings(st.session_state.settings)
    for key, default in {"quotes": [], "portfolio_quotes": [], "portfolio_history": {},
                         "portfolio_stop_history": {},
                         "momentum_ranking": [], "momentum_as_of": None,
                         "momentum_warning": None,
                         "momentum_comparison": None, "momentum_comparison_date": None,
                         "momentum_comparison_demo": False,
                         "momentum_sector_comparison": None,
                         "momentum_sector_3m_date": None, "momentum_sector_3m_demo": False,
                         "report": None, "refreshed_at": None}.items():
        if key not in st.session_state: st.session_state[key] = default
    sidebar()
    refreshed = st.session_state.refreshed_at.strftime("%H:%M:%S UTC") if st.session_state.refreshed_at else "Not yet refreshed"
    st.markdown(f'<div class="brief-title">OTP ALAPKEZELŐ MORNING BRIEF</div><div class="brief-sub">Last refresh: {refreshed}</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Refresh Data", type="primary", use_container_width=True): refresh_data(True)
    if c2.button("Generate Brief", use_container_width=True, disabled=not st.session_state.portfolio_quotes): generate_report()
    if c3.button("Preview Email", use_container_width=True, disabled=st.session_state.report is None): st.session_state.show_preview = True
    if c4.button("Send Email", use_container_width=True, disabled=st.session_state.report is None): st.session_state.confirm_send = True
    if st.session_state.portfolio_quotes:
        portfolio_quotes = st.session_state.portfolio_quotes

        st.subheader("Notable Portfolio Moves")
        notable = MarketDataService.notable_portfolio_moves(portfolio_quotes)
        if notable:
            notable_frame = pd.DataFrame([
                {"Ticker": q.ticker, "Company": q.name, "Last": format_price(q),
                 "Daily Change": display_change(q),
                 "Configured threshold": f"±{getattr(q, 'threshold_pct', None) or 2.0:.1f}%"}
                for q in notable
            ])
            st.dataframe(style_movement_table(notable_frame, "Daily Change"), hide_index=True, use_container_width=True)
        else:
            st.info("No portfolio position has breached its configured notable-move threshold.")

        st.markdown("#### Stop-loss monitor · drawdown from latest 6-month peak")
        stop_signals = select_stop_loss_signals(
            portfolio_quotes, st.session_state.portfolio_stop_history,
        )
        stop_candidates = [signal for signal in stop_signals if signal.status == "STOP"]
        watch_candidates = [signal for signal in stop_signals if signal.status == "WATCH"]
        stop_col, watch_col = st.columns(2)

        def signal_frame(signals):
            return pd.DataFrame([{
                "Ticker": signal.quote.ticker,
                "Company": signal.quote.name,
                "Last": f"${signal.current_price:,.2f}",
                "6M Peak": f"${signal.peak_price:,.2f}",
                "Peak date": signal.peak_date.strftime("%Y-%m-%d"),
                "Drawdown": f"-{signal.drawdown_pct:.2f}%",
            } for signal in signals])

        with stop_col:
            st.markdown("##### 🔴 STOP (−15% or worse)")
            if stop_candidates:
                st.dataframe(signal_frame(stop_candidates), hide_index=True, use_container_width=True)
            else:
                st.success("No position has reached the 15% stop-loss level.")
        with watch_col:
            st.markdown("##### 🟠 WATCH LIST (−10% to −15%)")
            if watch_candidates:
                st.dataframe(signal_frame(watch_candidates), hide_index=True, use_container_width=True)
            else:
                st.info("No position is currently in the 10–15% watch zone.")

        st.subheader("Top & Worst Performers")
        top_performers, worst_performers = select_portfolio_performers(portfolio_quotes)
        best_col, worst_col = st.columns(2)
        with best_col:
            st.markdown("#### Top Performers")
            top_frame = pd.DataFrame([
                {"Ticker": q.ticker, "Company": q.name, "Last": format_price(q),
                 "Daily Change": display_change(q)} for q in top_performers
            ])
            st.dataframe(style_movement_table(top_frame, "Daily Change"), hide_index=True, use_container_width=True)
        with worst_col:
            st.markdown("#### Worst Performers")
            worst_frame = pd.DataFrame([
                {"Ticker": q.ticker, "Company": q.name, "Last": format_price(q),
                 "Daily Change": display_change(q)} for q in worst_performers
            ])
            st.dataframe(style_movement_table(worst_frame, "Daily Change"), hide_index=True, use_container_width=True)

        st.subheader("Momentum Top 25")
        if st.session_state.momentum_warning:
            st.warning(st.session_state.momentum_warning)
        if st.session_state.momentum_ranking:
            momentum_frame = pd.DataFrame([{
                "Rank": int(row["rank"]),
                "Ticker": row["ticker"],
                "Sector": row.get("sector") or "Unknown",
                "Industry": row.get("industry") or "Unknown",
                "6M Return": f"{float(row['return_6m']):+.1%}",
                "12M Return": f"{float(row['return_12m']):+.1%}",
                "6M Score": f"{float(row['score_6m']):+.2f}",
                "12M Score": f"{float(row['score_12m']):+.2f}",
                "Momentum Score": f"{float(row['momentum_score']):+.2f}",
            } for row in st.session_state.momentum_ranking[:25]])
            st.caption(
                f"S&P 500 + Nasdaq-100 · as of {st.session_state.momentum_as_of} · full ranking saved to SQLite"
            )
            st.dataframe(momentum_frame, hide_index=True, use_container_width=True)
            comparison = st.session_state.momentum_comparison
            if comparison:
                st.markdown("#### Momentum Changes vs 1 Month Ago")
                if st.session_state.momentum_comparison_demo:
                    st.warning(
                        "DEMO comparison: no real month-old snapshot exists yet. "
                        "The preview uses generated prior ranks that are not saved to SQLite."
                    )
                else:
                    st.caption(
                        f"Compared with the latest saved ranking on or before the monthly target: "
                        f"{st.session_state.momentum_comparison_date}"
                    )
                entered_col, exited_col = st.columns(2)
                entered = comparison.get("entered", [])
                exited = comparison.get("exited", [])
                entered_col.success(
                    "NEW ENTRANTS\n\n" + (", ".join(entered) if entered else "None")
                )
                exited_col.error(
                    "DROPPED OUT\n\n" + (", ".join(exited) if exited else "None")
                )
                changed = [row for row in comparison.get("rank_changes", []) if row["change"]]
                if changed:
                    st.dataframe(pd.DataFrame([{
                        "Ticker": row["ticker"],
                        "Previous rank": row["previous_rank"],
                        "Current rank": row["current_rank"],
                        "Change": f"{row['change']:+d}",
                    } for row in changed]), hide_index=True, use_container_width=True)
            sector_rows = st.session_state.momentum_sector_comparison
            if sector_rows:
                st.markdown("#### Momentum Top 25 Sector Distribution")
                st.caption("Each bar represents 100% of the Top 25; one security equals 4 percentage points.")
                sector_figure = go.Figure()
                previous_label = (
                    "1M Ago (Demo)" if st.session_state.momentum_comparison_demo
                    else f"1M Ago ({st.session_state.momentum_comparison_date})"
                )
                three_month_label = (
                    "3M Ago (Demo)" if st.session_state.momentum_sector_3m_demo
                    else f"3M Ago ({st.session_state.momentum_sector_3m_date})"
                )
                for row in sector_rows:
                    sector_figure.add_trace(go.Bar(
                        name=row["sector"], orientation="h",
                        y=["Current", previous_label, three_month_label],
                        x=[row["current_pct"], row["previous_pct"], row["three_month_pct"]],
                        marker_color=row["color"],
                        text=[
                            (f"{row['sector']} {row['current_pct']:.0f}%" if row["current_pct"] >= 16
                             else f"{row['current_pct']:.0f}%" if row["current_pct"] >= 8 else ""),
                            (f"{row['sector']} {row['previous_pct']:.0f}%" if row["previous_pct"] >= 16
                             else f"{row['previous_pct']:.0f}%" if row["previous_pct"] >= 8 else ""),
                            (f"{row['sector']} {row['three_month_pct']:.0f}%" if row["three_month_pct"] >= 16
                             else f"{row['three_month_pct']:.0f}%" if row["three_month_pct"] >= 8 else ""),
                        ],
                        textposition="inside",
                        hovertemplate=(
                            f"{row['sector']}<br>%{{y}}: %{{x:.1f}}%<extra></extra>"
                        ),
                    ))
                sector_figure.update_layout(
                    barmode="stack", height=280, margin={"l": 10, "r": 10, "t": 10, "b": 20},
                    xaxis={"range": [0, 100], "ticksuffix": "%", "title": None},
                    yaxis={"title": None}, legend={"orientation": "h", "y": -0.25},
                    paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                )
                st.plotly_chart(
                    sector_figure, use_container_width=True, config={"displayModeBar": False},
                )
                st.dataframe(pd.DataFrame([{
                    "Sector": row["sector"],
                    "Current": f"{row['current_pct']:.1f}%",
                    "1M Ago": f"{row['previous_pct']:.1f}%",
                    "Δ 1M": f"{row['change_pp']:+.1f} pp",
                    "3M Ago": f"{row['three_month_pct']:.1f}%",
                    "Δ 3M": f"{row['change_3m_pp']:+.1f} pp",
                } for row in sector_rows]), hide_index=True, use_container_width=True)
        elif not st.session_state.momentum_warning:
            st.info("Refresh Data to calculate the momentum ranking.")

        st.subheader("Portfolio Status")
        st.caption("Latest price and daily move for enabled portfolio securities")
        available = [q for q in portfolio_quotes if q.percentage_change is not None]
        up_count = sum(1 for q in available if (q.percentage_change or 0) > 0)
        down_count = sum(1 for q in available if (q.percentage_change or 0) < 0)
        unchanged_count = sum(1 for q in available if q.percentage_change == 0)
        unavailable_count = len(portfolio_quotes) - len(available)
        pc1, pc2, pc3, pc4, pc5 = st.columns(5)
        pc1.markdown(f'<div class="status-card"><span>POSITIONS</span><strong>{len(portfolio_quotes)}</strong></div>', unsafe_allow_html=True)
        pc2.markdown(f'<div class="status-card positive"><span>UP</span><strong>▲ {up_count}</strong></div>', unsafe_allow_html=True)
        pc3.markdown(f'<div class="status-card negative"><span>DOWN</span><strong>▼ {down_count}</strong></div>', unsafe_allow_html=True)
        pc4.markdown(f'<div class="status-card"><span>UNCHANGED</span><strong>• {unchanged_count}</strong></div>', unsafe_allow_html=True)
        pc5.markdown(f'<div class="status-card"><span>N/A</span><strong>{unavailable_count}</strong></div>', unsafe_allow_html=True)
        portfolio_frame = pd.DataFrame([
            {"Ticker": q.ticker, "Company": q.name, "Last": format_price(q),
             "Daily Change": display_change(q), "Notable at": f"±{getattr(q, 'threshold_pct', None) or 2.0:.1f}%",
             "Status": q.error or "Available"}
            for q in portfolio_quotes
        ])
        st.dataframe(style_movement_table(portfolio_frame, "Daily Change"), hide_index=True, use_container_width=True)

        st.subheader("Historical Price Charts")
        st.caption(f"Adjusted daily closing prices · {getattr(st.session_state.settings, 'chart_period', '3mo')}")
        history_tabs = st.tabs([quote.ticker for quote in portfolio_quotes])
        for tab, quote in zip(history_tabs, portfolio_quotes):
            with tab:
                history = st.session_state.portfolio_history.get(quote.ticker)
                if history is None or history.empty:
                    st.warning(f"No historical price data is available for {quote.ticker}.")
                    continue
                fig = go.Figure(go.Scatter(
                    x=history["Date"], y=history["Price"], mode="lines",
                    line={"color": "#3a7059", "width": 2.5},
                    hovertemplate="%{x|%Y-%m-%d}<br>Price: %{y:,.2f}<extra></extra>",
                ))
                fig.update_layout(
                    height=360, margin={"l": 10, "r": 10, "t": 35, "b": 10},
                    title={"text": f"{quote.ticker} · {quote.name}", "font": {"size": 16, "color": "#3a7059"}},
                    paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", hovermode="x unified",
                    xaxis={"title": None, "showgrid": False, "rangeslider": {"visible": False}},
                    yaxis={"title": "Adjusted price", "gridcolor": "#e3ebe6"},
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    report = st.session_state.report
    if report:
        st.divider(); st.subheader("Email")
        if getattr(report, "chart_warning", None):
            st.warning(report.chart_warning)
        st.write(f"**Recipient:** {st.session_state.settings.recipient or 'Not configured'}  \n**Subject:** {report.subject}  \n**Generated:** {report.generated_at:%Y-%m-%d %H:%M %Z}")
        if st.session_state.get("show_preview"):
            st.components.v1.html(browser_preview_html(report), height=900, scrolling=True)
        left, right = st.columns(2)
        sender = OutlookSender()
        if left.button("OPEN IN OUTLOOK", use_container_width=True):
            try: sender.display_email(sender.create_email(st.session_state.settings.recipient, report.subject, report.html, getattr(report, "inline_images", []))); database().record_report(st.session_state.settings.recipient, report.subject, report.article_count, "opened"); st.success("Draft opened in Outlook.")
            except (ValueError, OutlookError) as exc: st.error(str(exc))
        if right.button("SEND EMAIL DIRECTLY", type="primary", use_container_width=True): st.session_state.confirm_send = True
        if st.session_state.get("confirm_send"):
            st.warning("Direct sending is immediate. Confirm the recipient and subject above.")
            if st.button("Confirm direct send"):
                try: sender.send_email(sender.create_email(st.session_state.settings.recipient, report.subject, report.html, getattr(report, "inline_images", []))); database().record_report(st.session_state.settings.recipient, report.subject, report.article_count, "sent"); st.success("Email sent through Outlook."); st.session_state.confirm_send = False
                except (ValueError, OutlookError) as exc: st.error(str(exc))
    elif not st.session_state.refreshed_at:
        st.info("Select Refresh Data to retrieve portfolio and momentum prices. Nothing is sent automatically.")


if __name__ == "__main__":
    logger.info("Application startup")
    main()

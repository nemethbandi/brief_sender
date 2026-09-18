from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.settings import AppSettings, load_settings, save_settings
from data.market_data import MarketDataService
from mail.outlook_sender import OutlookError, OutlookSender
from processing.market_analyzer import (
    format_change, format_price, select_portfolio_performers, select_stop_loss_signals,
)
from storage.database import Database
from workflows.daily_brief import load_brief_data, build_report
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
    return Database()


def refresh_data() -> None:
    # Invalidate the old snapshot before attempting a refresh.
    st.session_state.report = None
    st.session_state.brief_data = None
    st.session_state.portfolio_rows = []
    st.session_state.portfolio_quotes = []
    st.session_state.portfolio_history = {}
    st.session_state.portfolio_stop_history = {}
    st.session_state.momentum_ranking = []
    st.session_state.momentum_comparison = None
    st.session_state.momentum_sector_comparison = None
    st.session_state.momentum_warning = None
    st.session_state.refreshed_at = None
    st.session_state.confirm_send = False
    st.session_state.show_preview = False
    settings = st.session_state.settings
    with st.spinner("Retrieving internal LSEG portfolio and momentum data..."):
        try:
            data = load_brief_data(datetime.now(ZoneInfo(settings.timezone)), settings)
        except Exception as exc:
            logger.exception("Internal LSEG refresh failed")
            st.error(f"LSEG data could not be loaded: {exc}")
            return
    st.session_state.brief_data = data
    st.session_state.portfolio_rows = data.portfolio_rows
    st.session_state.portfolio_quotes = data.quotes
    st.session_state.portfolio_history = data.histories
    st.session_state.portfolio_stop_history = data.stop_histories
    st.session_state.momentum_ranking = data.momentum["ranking"].to_dict("records")
    st.session_state.momentum_as_of = data.as_of.date().isoformat()
    st.session_state.momentum_comparison = data.momentum["changes"]
    st.session_state.momentum_comparison_date = data.momentum["one_month_date"]
    st.session_state.momentum_sector_comparison = data.momentum["sector_comparison"]
    st.session_state.momentum_sector_3m_date = data.momentum["three_month_date"]
    st.session_state.momentum_warning = data.momentum_warning
    st.session_state.refreshed_at = data.as_of


def generate_report() -> None:
    st.session_state.report = build_report(st.session_state.brief_data, st.session_state.settings)


def sidebar() -> None:
    settings = st.session_state.settings
    with st.sidebar:
        st.header("Portfolio")
        st.caption("Source: internal LSEG / Datastream. Portfolio positions come from the SQL ISIN list.")
        if st.session_state.portfolio_rows:
            st.dataframe(pd.DataFrame(st.session_state.portfolio_rows), hide_index=True, use_container_width=True)
        else:
            st.info("Refresh Data to load the portfolio.")
        with st.expander("Settings"):
            recipient = st.text_input("Portfolio Manager email", settings.recipient)
            title = st.text_input("Report title", settings.report_title)
            periods = ["1mo", "3mo", "6mo", "1y", "2y"]
            saved_period = getattr(settings, "chart_period", "3mo")
            chart_period = st.selectbox("Historical chart period", periods,
                                        index=periods.index(saved_period) if saved_period in periods else 1)
            if st.button("Save settings", use_container_width=True):
                st.session_state.settings = AppSettings(recipient=recipient.strip(), report_title=title.strip() or "OTP Alapkezelő Morning Brief",
                    chart_period=chart_period,
                    timezone=settings.timezone)
                save_settings(st.session_state.settings); st.success("Settings saved.")


def main() -> None:
    if "settings" not in st.session_state: st.session_state.settings = load_settings()
    if st.session_state.settings.report_title == "Morning Market Brief":
        st.session_state.settings.report_title = "OTP Alapkezelő Morning Brief"
        save_settings(st.session_state.settings)
    for key, default in {"brief_data": None, "portfolio_rows": [], "portfolio_quotes": [], "portfolio_history": {},
                         "portfolio_stop_history": {},
                         "momentum_ranking": [], "momentum_as_of": None,
                         "momentum_warning": None,
                         "momentum_comparison": None, "momentum_comparison_date": None,
                         "momentum_sector_comparison": None,
                         "momentum_sector_3m_date": None,
                         "report": None, "refreshed_at": None}.items():
        if key not in st.session_state: st.session_state[key] = default
    refreshed = st.session_state.refreshed_at.strftime("%H:%M:%S %Z") if st.session_state.refreshed_at else "Not yet refreshed"
    st.markdown(f'<div class="brief-title">OTP ALAPKEZELŐ MORNING BRIEF</div><div class="brief-sub">Last refresh: {refreshed}</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Refresh Data", type="primary", use_container_width=True): refresh_data()
    if c2.button("Generate Brief", use_container_width=True, disabled=not st.session_state.portfolio_quotes): generate_report()
    if c3.button("Preview Email", use_container_width=True, disabled=st.session_state.report is None): st.session_state.show_preview = True
    if c4.button("Send Email", use_container_width=True, disabled=st.session_state.report is None): st.session_state.confirm_send = True
    sidebar()
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
                f"S&P 500 · as of {st.session_state.momentum_as_of} · full ranking saved to SQLite"
            )
            st.dataframe(momentum_frame, hide_index=True, use_container_width=True)
            comparison = st.session_state.momentum_comparison
            if comparison:
                st.markdown("#### Momentum Changes vs 1 Month Ago")
                st.caption(f"Compared with saved ranking: {st.session_state.momentum_comparison_date}")
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
                st.markdown("#### Momentum Top 100 Sector Distribution")
                st.caption("Top 100 = top 20% of 500 ranked securities. Equal weights: 1% each when 100 are available.")
                sample_size = sum(row["current_count"] for row in sector_rows)
                if sample_size < 100:
                    st.info(f"Only {sample_size} ranked securities are available; weights use that sample.")
                sector_figure = go.Figure()
                previous_date = st.session_state.momentum_comparison_date
                three_month_date = st.session_state.momentum_sector_3m_date
                previous_label = f"1M Ago ({previous_date})"
                three_month_label = f"3M Ago ({three_month_date})"
                if not previous_date or not three_month_date:
                    st.info("Historical comparisons become available as real daily snapshots accumulate.")
                for row in sector_rows:
                    sector_figure.add_trace(go.Bar(
                        name=row["sector"], orientation="h",
                        y=["Current"] + ([previous_label] if previous_date else []) + ([three_month_label] if three_month_date else []),
                        x=[row["current_pct"]] + ([row["previous_pct"]] if previous_date else []) + ([row["three_month_pct"]] if three_month_date else []),
                        marker_color=row["color"],
                        text=[f"{row['current_pct']:.0f}%"]
                             + ([f"{row['previous_pct']:.0f}%"] if previous_date else [])
                             + ([f"{row['three_month_pct']:.0f}%"] if three_month_date else []),
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
                    "1M Ago": f"{row['previous_pct']:.1f}%" if previous_date else "N/A",
                    "Δ 1M": f"{row['change_pp']:+.1f} pp" if previous_date else "N/A",
                    "3M Ago": f"{row['three_month_pct']:.1f}%" if three_month_date else "N/A",
                    "Δ 3M": f"{row['change_3m_pp']:+.1f} pp" if three_month_date else "N/A",
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
        st.caption(f"Daily closing prices · {getattr(st.session_state.settings, 'chart_period', '3mo')}")
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
                    yaxis={"title": "Price", "gridcolor": "#e3ebe6"},
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

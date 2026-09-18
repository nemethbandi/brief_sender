# OTP Alapkezelő Morning Brief

A local, deterministic portfolio-status and momentum briefing application for investment teams. It uses traditional market APIs and contains **no AI, LLM, generative model, or related API integration**.

## Windows installation

Python 3.11 or newer and classic Microsoft Outlook Desktop are required. In PowerShell:

```powershell
cd C:\path\to\update_email
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

If script activation is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` according to your corporate policy, or invoke `.venv\Scripts\python.exe` and `.venv\Scripts\streamlit.exe` directly.

## Daily workflow

1. Set the Portfolio Manager email under **Settings**.
2. Select **Refresh Data**. Provider failures are shown as unavailable and do not stop other instruments.
3. Select **Generate Brief**, then **Preview Email**.
4. Use **Open in Outlook** (recommended) to inspect a draft. **Send Email Directly** always requires a separate confirmation.

Nothing is sent automatically. Credentials and Outlook passwords are never stored.

During **Refresh Data**, the application also builds the combined S&P 500 and Nasdaq-100
universe, calculates the full risk-adjusted momentum ranking, and persists that day's
snapshot before an email can be generated. The email remains available if the independent
momentum download fails.

## Configuration

- **Portfolio:** edit tickers, company names, enabled status and a separate notable-move threshold for every position. Saved entries live in SQLite.
- **Move thresholds:** the `Notable threshold (%)` column is evaluated independently for every enabled portfolio ticker. A move is notable when its absolute daily percentage change reaches that ticker's threshold.
- **Momentum:** the `Momentum Top 25` section uses 126- and 252-trading-day returns relative to the valid-universe mean, divided by each stock's annualized realized volatility. The two risk-adjusted scores are averaged without a 12-1 skip-month adjustment.
- **Momentum history:** the full daily ranking is stored in `storage/momentum.db` by default. Set `MOMENTUM_DB_PATH` to an absolute persistent path to override it. Writes use `(as_of_date, ticker)` UPSERT keys, so rerunning a date is safe.
- **Monthly momentum changes:** below Momentum Top 25, the brief compares the current Top 25 with the latest saved ranking on or before the date one calendar month earlier. Until such a snapshot exists, a clearly marked deterministic demo comparison is generated in memory for testing; demo rows are never written to SQLite. The system switches automatically to real history as soon as it is available.
- **Sector distribution:** Yahoo `sector` and detailed `industry` metadata is cached for 30 days in `momentum.db` and copied into each daily Top-25 ranking snapshot. The dashboard and email show current, one-month-prior and three-month-prior 100% stacked sector allocations, including 1M and 3M percentage-point changes. Missing classifications remain visible as `Unknown`, so every bar still totals exactly 100%. A missing 1M or 3M history is shown as a clearly marked, non-persisted demo and switches automatically to real history when available.
- **Secrets:** copy `.env.example` to `.env` if desired. Do not commit `.env`.

The initial portfolio list is in `config/`. On first run, the portfolio is seeded into `storage/market_brief.db`. Delete only that database if you intentionally want to reset all local persisted data.

## Data behavior

Yahoo Finance is the default portfolio and momentum price provider and may delay, omit or change access to instruments. Values are never invented: an unsuccessful quote is shown as `N/A`. News, general market, futures and macro sections are intentionally excluded.

The dashboard and email lead with **Notable Portfolio Moves**, followed by the three best and three worst daily performers, **Momentum Top 25**, and the full **Portfolio Status**. Instruments without an available daily percentage change are excluded from the performer ranking.

The dashboard also provides an OTP-green Plotly historical price chart for every enabled portfolio ticker. Settings supports 1-month, 3-month, 6-month, 1-year and 2-year windows; prices are daily and adjusted for splits and distributions where Yahoo Finance supplies the adjustment. When the brief is generated, the top-three and worst-three performer charts are rendered into compact PNG panels and embedded in Outlook as hidden CID attachments. Plotly 6.1.1+, Kaleido 1.x and a locally installed Chrome/Chromium browser are required for email chart rendering; chart failure does not prevent the text brief from being generated or sent.

Network results are cached for roughly three minutes (portfolio market data) and one hour (momentum). **Refresh Data** safely clears those caches. SQLite stores portfolio configuration, full daily momentum rankings, and report-send metadata, but no passwords. Legacy news rows may remain in an existing `market_brief.db`, but the application no longer retrieves or displays news.

## Outlook requirements

The transport uses `pywin32` COM automation and requires compatible classic Outlook Desktop on the same Windows user session. New Outlook may not expose this COM interface. Start/configure classic Outlook and ensure the corporate security policy permits automation. Email rendering is table-based with inline CSS for Outlook compatibility. The report builder is independent of Outlook, so a future Microsoft Graph or approved SMTP transport can be added without changing report logic.

## Tests

Tests use fake data and never access the internet:

```powershell
pytest -q
```

They cover percentage and momentum calculations, ranking validity, historical momentum persistence/idempotency, provider replacement, HTML escaping, chart/email generation, and portfolio persistence.

## Troubleshooting

- **No portfolio price:** verify internet/proxy access and the portfolio ticker. Other positions remain available.
- **Outlook error:** use classic Outlook Desktop, open it once interactively, confirm the profile, and verify `pywin32` was installed in the active environment.
- **Empty report:** refresh first; Generate Brief stays disabled until quote results exist (including gracefully unavailable rows).
- **Logs:** diagnostics are written to `logs/app.log`; recipient credentials and passwords are not logged.

## Architecture

`data/` contains replaceable market providers, `processing/` contains deterministic analysis (including the pure momentum calculation), `reports/` owns Outlook-safe rendering, `mail/` owns transport, `storage/` owns SQLite, and `app.py` only coordinates the Streamlit experience. The transport folder is intentionally named `mail`, because naming a top-level Python package `email` would shadow the standard library and break HTTP dependencies.

Momentum helpers accept an explicit `as_of_date`, and the Airflow workflow passes its logical execution date. `MomentumDatabase.get_latest_ranking_on_or_before()` and `compare_rankings()` support the historical Top-25 comparisons without requiring an exact trading-day match.

## Market data providers

Market data access is adapter-based. `MARKET_DATA_PROVIDER=yahoo` preserves the
current behavior; `MARKET_DATA_PROVIDER=lseg` selects the optional LSEG Data
Library adapter. Portfolio quotes/history, the S&P 500 momentum universe and
history, and sector/industry metadata use the same selected provider, while the
report layout, momentum database and email delivery remain unchanged.

For an internal SQL/Datastream extraction, inject `DataFrameMarketDataProvider`
into `run_daily_brief()`. It expects separate normalized S&P universe, portfolio
price and momentum-value DataFrames and does not prescribe how they are loaded.
The four intentionally empty integration functions are collected in
`data/internal_data_source.py`; after implementing them, select
`MARKET_DATA_PROVIDER=internal_dataframe` and the existing DAG uses them.

See `docs/market_data_adapter.md` for the normalized data contract, LSEG field
configuration, RIC mapping and installation details.

## Airflow deployment

The standalone Airflow entry point is `dags/morning_brief_dag.py`. It runs daily at
07:00 Europe/Budapest by default and uses Airflow's `data_interval_end` as the logical
brief/ranking date. Override the cron expression with `MORNING_BRIEF_SCHEDULE`.

The scheduled workflow is implemented in `workflows/daily_brief.py` and does not import
Streamlit. Its order is:

1. load the currently enabled local portfolio;
2. retrieve portfolio prices and histories;
3. calculate and UPSERT the full momentum ranking;
4. build the same HTML report and inline charts as the local application;
5. call the existing `_send_email()` utility.

The email integration intentionally contains no SMTP/MIME implementation. It imports the
existing utility exactly as:

```python
from send_email import _send_email
```

`send_email.py` must therefore be on the Airflow worker's Python path, and its existing
`/opt/airflow/dags/Common/Mailing/config.json` must be present. The workflow translates
report image references to the utility's ordered `cid:inline_image_1`,
`cid:inline_image_2`, ... convention. A return value other than
`Email sent successfully!` raises an exception so Airflow retries and marks the task failed.

Configure comma-separated recipients with Airflow Variables:

- `morning_brief_to`
- `morning_brief_cc` (optional)
- `morning_brief_bcc` (optional)

The equivalent `MORNING_BRIEF_TO`, `MORNING_BRIEF_CC`, and `MORNING_BRIEF_BCC`
environment variables are fallbacks. If no `To` variable/environment value exists, the
local `PORTFOLIO_MANAGER_EMAIL`/user setting is used.

For a writable persistent Airflow volume, set for example:

```text
MARKET_BRIEF_DB_PATH=/opt/airflow/data/market_brief.db
MOMENTUM_DB_PATH=/opt/airflow/data/momentum.db
```

The current portfolio source is isolated in `load_current_portfolio()` so it can later be
replaced by the planned SQL query without changing momentum, report, chart, DAG, or email
logic. `run_daily_brief(..., portfolio_rows=...)` already accepts externally queried rows.

Mount the repository root so it is on the Airflow Python path (for example directly at
`/opt/airflow/dags`), install `requirements-airflow.txt` using the deployment's matching
Airflow constraints, and provide Chrome/Chromium for Kaleido chart rendering. The local
Streamlit application can remain installed for manual diagnostics but is not used by the DAG.

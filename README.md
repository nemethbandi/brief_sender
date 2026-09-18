# OTP Alapkezelő Morning Brief

Portfolio, stop-loss and S&P 500 momentum reporting from an internal LSEG /
Datastream feed. Streamlit and Airflow use the same loading and report code.

## Connect your data

Fill the four functions in `data/internal_data_source.py`:

| Function | Output |
| --- | --- |
| `load_portfolio_isins(as_of)` | Unique held ISINs from your SQL query |
| `load_sp500_universe(as_of)` | DataFrame: `ticker`, `data_id`; optional `sector`, `industry` |
| `load_portfolio_prices(isins, as_of)` | DataFrame: `isin`, `ticker`, `data_id`, `date`, `close`; optional `name`, `threshold_pct` |
| `load_momentum_prices(data_ids, as_of)` | DataFrame: `data_id`, `date`, `value` |

Return at least six months of portfolio daily closes and preferably 15 months
of adjusted prices / Return Index values for momentum (at least 253 valid daily
observations per security). For longer portfolio chart windows, supply the
corresponding history. See [the full contract](docs/market_data_adapter.md).

This is the only market data source. Until the loaders are implemented,
refreshing reports a missing-data error. Old `MARKET_DATA_PROVIDER` and `LSEG_*`
adapter settings can be removed; the application no longer reads them.

## Test in Streamlit

Python 3.11 or newer:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

1. Implement the loaders and their internal connection settings.
2. Select **Refresh Data** to load portfolio prices, charts and momentum.
3. Select **Generate Brief**, then **Preview Email** to inspect the report.
4. To send locally, set the recipient under Settings. Classic Outlook Desktop
   on Windows is needed only for opening a draft or direct Outlook sending.

Refreshing and previewing do not send email. Direct sending requires confirmation.
The portfolio comes from the SQL ISIN list; there is no separate local portfolio
editor. Set each security's notable-move threshold with `threshold_pct` in the
portfolio price DataFrame (default 2%).

The optional `.env` file is loaded from the repository root; `.env.example`
contains the supported settings. Credentials must not be committed.

## Momentum and history

Momentum uses 126- and 252-trading-day returns relative to the valid-universe
mean, divided by each stock's annualized realized volatility. The two scores
are averaged, without a skip-month adjustment.

Daily rankings are upserted by `(as_of_date, ticker)` into
`storage/momentum_lseg.db`. This new default keeps earlier market-source history
separate. Existing databases are not deleted. If you override `MOMENTUM_DB_PATH`,
choose a dedicated LSEG database rather than an old mixed-source file.

Monthly and quarterly comparisons use actual saved snapshots. Missing periods
show N/A until sufficient history exists. Sector/industry metadata comes from
the internal universe and is cached for 30 days. Missing classifications are
shown as `Unknown`. An unavailable momentum calculation is reported without
blocking a valid portfolio report.

Email chart images need Chrome/Chromium for Kaleido. If chart rendering fails,
the text report remains available with a warning.

## Airflow deployment

The DAG is `dags/morning_brief_dag.py`, ID `otp_alapkezelo_morning_brief`.
Its default schedule is daily at 07:00 Europe/Budapest, with catchup disabled.
The report date comes from `data_interval_end`; loaders must respect `as_of`.

Make the project importable on the worker (for example, place it under
`/opt/airflow/dags`) and install `requirements-airflow.txt` with your deployment's
matching Airflow constraints. Airflow itself is supplied by the deployment.
Install your internal SQL/LSEG client dependencies separately.

The worker must be able to import `from send_email import _send_email`.
This existing deployment utility and its mailing configuration must be supplied
by your environment. The project contains no SMTP implementation or credentials.
The utility must return `Email sent successfully!` on success.
Streamlit and Outlook are not used by the DAG.

Set comma-separated recipients with Airflow Variables:

- `morning_brief_to`
- `morning_brief_cc` (optional)
- `morning_brief_bcc` (optional)

Fallback environment variables are `MORNING_BRIEF_TO`, `MORNING_BRIEF_CC`, and
`MORNING_BRIEF_BCC`. If no To value is supplied, `PORTFOLIO_MANAGER_EMAIL` or the
local user-setting recipient is used. No recipient causes a delivery error.

Set persistent writable paths, for example:

```dotenv
MOMENTUM_DB_PATH=/opt/airflow/data/momentum_lseg.db
MARKET_BRIEF_DB_PATH=/opt/airflow/data/market_brief.db
```

Test with scheduling paused and your own explicit To address; check CC/BCC too.
A manual DAG run sends a real email. There is no dry-run flag. To preview without
sending, use Streamlit or call `load_brief_data()` and `build_report()` directly.
Neither function sends mail; loading does persist the momentum snapshot.

## Automated checks

```powershell
python -m pytest -q
```

Tests use fixture data and temporary databases, with email delivery stubbed.
They cover the internal loaders, shared workflow, Streamlit refresh and preview,
failed-refresh invalidation, momentum, persistence and report rendering.
These checks do not verify access to your live LSEG, SQL or Airflow environment.

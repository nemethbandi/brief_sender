# Internal LSEG / Datastream data contract

The only market data source is `data/internal_data_source.py`. Both Streamlit
and Airflow call `workflows.daily_brief.load_brief_data()`, which loads and
validates the same four loader outputs. No provider selection is needed.

Implement these functions in `data/internal_data_source.py`:

```python
load_portfolio_isins(as_of)
load_sp500_universe(as_of)
load_portfolio_prices(isins, as_of)
load_momentum_prices(data_ids, as_of)
```

SQL, authentication and your internal LSEG/Datastream client belong inside
these functions. Keep credentials in your environment or secret store.
The first function returns a list; the remaining three return DataFrames.
The templates are empty and deliberately fail until filled.

### 1. Portfolio ISIN list

The colleague-owned SQL loader returns the unique held ISINs:

```python
["US5949181045", "US67066G1040"]
```

`load_portfolio_isins(as_of)` removes SQL details from the rest of the project.
The returned ISINs are passed directly to `load_portfolio_prices()`.

### 2. S&P 500 universe

One row per constituent:

```text
ticker | data_id | sector             | industry
MSFT   | MSFT.O  | Technology         | Software
BRK-B  | BRKb.N  | Financial Services | Insurance
```

Required columns: `ticker`, `data_id`. `sector` and `industry` are optional but
strongly recommended; missing values become `Unknown`.

### 3. Portfolio reference data and daily prices

Long format, at least six months, one row per instrument and trading day:

```text
isin         | ticker | data_id | name      | threshold_pct | date       | close
US5949181045 | MSFT   | MSFT.O  | Microsoft | 2.0          | 2026-09-01 | 505.12
US5949181045 | MSFT   | MSFT.O  | Microsoft | 2.0          | 2026-09-02 | 509.30
```

Required columns: `isin`, `ticker`, `data_id`, `date`, `close`. `name` and
`threshold_pct` are optional and default to the ticker and 2%. These values
resolve the SQL ISINs and drive current/previous close, notable moves, the
stop-loss tracker and portfolio charts.

### 4. Momentum daily values

Long format, preferably 15 months:

```text
data_id | date       | value
MSFT.O  | 2026-09-01 | 1245.31
MSFT.O  | 2026-09-02 | 1256.28
```

Required columns: `data_id`, `date`, `value`. `value` should be a
corporate-action-adjusted price or Datastream Return Index. At least 253 valid
daily observations are needed per security.

## Dates and validation

`as_of` is the requested report date/time in the configured timezone. Each loader
must return the portfolio/universe for that date and history ending on or before
that date. Do not include future observations when replaying historical runs.

Prices are long-format: one row per instrument per trading day. Duplicate
`(data_id, date)` values and ambiguous identifier mappings are rejected. Every
held ISIN must resolve to one ticker and data identifier. Use consistent
`data_id` values across all three DataFrames and consistent classification names.

For portfolio charts longer than six months, return enough history for the
selected chart period (up to two years). The `close` values are displayed as
supplied; the application does not adjust them. The momentum `value` series
must already contain adjusted prices or a Return Index.

## Streamlit and Airflow

Streamlit Refresh Data loads a fresh snapshot and persists its momentum ranking.
Generate Brief renders that snapshot without another data request; Preview Email
displays it without sending. Portfolio membership is read-only and comes from SQL.

Airflow runs the same loading and report code, then calls the deployment's
`send_email._send_email`. It sends immediately to the configured recipients.
See the README for deployment and recipient settings.

No data-source environment variable or separate LSEG SDK configuration is used
by this repository. Install whatever client your loader implementations need.
Missing data never triggers a fallback to another provider.

# Market data adapter contract

The report, momentum calculation, database and email renderer are vendor-neutral.
They consume `MarketDataProvider`; vendor-specific access is isolated in an
adapter selected by `MARKET_DATA_PROVIDER`.

For the planned internal feed, the preferred boundary is
`DataFrameMarketDataProvider`. In this mode the project does not authenticate to
or download from LSEG itself. Your extraction code produces one unique ISIN
list and three DataFrames; the provider validates them and feeds the unchanged
report logic.

## Preferred internal DataFrame input

The only extraction file you need to edit is
`data/internal_data_source.py`. Fill in these four functions:

```python
load_portfolio_isins(as_of)
load_sp500_universe(as_of)
load_portfolio_prices(isins, as_of)
load_momentum_prices(data_ids, as_of)
```

Leave the remaining provider and workflow code unchanged. Once all four loaders
return populated DataFrames, set:

```text
MARKET_DATA_PROVIDER=internal_dataframe
```

The unchanged Airflow DAG will then use the internal DataFrames automatically.

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

### Airflow wiring point

Keep extraction and report generation in the same task unless the DataFrames
are persisted outside XCom. After the four functions are filled, the existing
DAG wiring is already complete:

```python
MARKET_DATA_PROVIDER=internal_dataframe
```

Duplicate `(data_id, date)` price rows, ambiguous universe mappings and missing
required columns fail validation before the momentum database or email is
updated.

## Required datasets

The provider must supply four normalized datasets:

1. **Portfolio quotes**
   - canonical/display ticker;
   - latest daily close;
   - previous daily close;
   - timestamp.
2. **Portfolio history**
   - at least six months of daily closes for stop-loss tracking;
   - the configured chart period (currently three months) for charts.
3. **Momentum universe and history**
   - current S&P 500 constituents;
   - canonical ticker and vendor instrument identifier (LSEG RIC);
   - at least 253 valid daily closes per security; 15 calendar months are
     requested to tolerate holidays and missing observations.
   - a split/corporate-action-adjusted price series or Datastream Return Index
     should be preferred over a raw unadjusted close.
4. **Classification metadata**
   - canonical ticker;
   - sector;
   - industry.

The workflow requests metadata only for the current, one-month and three-month
Top 25 members and caches successful values for 30 days. The adapter still
supports metadata retrieval for any security in the universe.

## Normalized Python shapes

Momentum universe:

```python
["AAPL", "BRK-B", "MSFT"]
```

Momentum price history: a `pandas.DataFrame` with dates in the index and
canonical tickers in columns:

```text
Date        AAPL    BRK-B   MSFT
2025-06-02  201.7   495.2   460.1
```

Security metadata:

```python
[
    {"ticker": "MSFT", "sector": "Technology", "industry": "Software"},
]
```

The workflow derives portfolio rows from the reference columns repeated in the
portfolio-price DataFrame:

```python
[
    {
        "ticker": "MSFT",       # stable identifier shown in email/DB
        "data_id": "MSFT.O",    # LSEG RIC used for data retrieval
        "name": "Microsoft",
        "threshold_pct": 2.0,
    }
]
```

`ric` is also accepted as an input alias and is normalized to `data_id`.

## Providers

Yahoo remains the default:

```text
MARKET_DATA_PROVIDER=yahoo
```

LSEG Data Library:

```text
MARKET_DATA_PROVIDER=lseg
LD_LIB_CONFIG_PATH=/opt/airflow/config/lseg-data.config.json
LSEG_SP500_UNIVERSE=0#.SPX
```

Install the optional dependency from `requirements-lseg.txt`. The adapter opens
the configured LSEG session lazily on its first request. Credentials remain in
the LSEG configuration/session infrastructure and are not stored in this
project.

The default LSEG fields are configurable because exact field availability is
entitlement-dependent:

```text
LSEG_CLOSE_FIELD=TRDPRC_1
LSEG_MOMENTUM_FIELD=TRDPRC_1
LSEG_RIC_FIELD=TR.RIC
LSEG_SYMBOL_FIELD=TR.ExchangeTicker
LSEG_SECTOR_FIELD=TR.TRBCEconomicSector
LSEG_INDUSTRY_FIELD=TR.TRBCIndustry
```

`LSEG_CLOSE_FIELD` drives portfolio quotes and charts.
`LSEG_MOMENTUM_FIELD` drives the 6M/12M momentum calculation and should be set
to the entitled adjusted-price or Datastream Return Index field agreed with the
LSEG data owner. Its default only mirrors `LSEG_CLOSE_FIELD` so the adapter can
be smoke-tested before the final content mapping is supplied.

The S&P 500 chain result is rejected when it contains fewer than
`LSEG_MIN_UNIVERSE_SIZE` instruments (default 400), preventing an entitlement or
partial-response problem from silently becoming the new momentum snapshot.

The direct `lseg.data` adapter is optional. It can be ignored when the internal
system performs the extraction and supplies the three normalized DataFrames.
In that case `lseg-data` does not need to be installed by this project.

If the company exposes Datastream through an internal wrapper instead of
`lseg.data`, implement the same methods on a `MarketDataProvider` subclass or
inject the internal LSEG-compatible client into `LSEGDataProvider`. No report,
momentum, persistence or email code needs to change.

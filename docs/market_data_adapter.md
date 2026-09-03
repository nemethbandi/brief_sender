# Market data adapter contract

The report, momentum calculation, database and email renderer are vendor-neutral.
They consume `MarketDataProvider`; vendor-specific access is isolated in an
adapter selected by `MARKET_DATA_PROVIDER`.

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

Portfolio rows from the future SQL query may carry a separate LSEG identifier:

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

If the company exposes Datastream through an internal wrapper instead of
`lseg.data`, implement the same methods on a `MarketDataProvider` subclass or
inject the internal LSEG-compatible client into `LSEGDataProvider`. No report,
momentum, persistence or email code needs to change.

"""Fill in only the four loader functions in this file.

The rest of the project consumes their normalized DataFrames through
DataFrameMarketDataProvider. Authentication, SQL and internal LSEG/Datastream
access belong inside these functions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import pandas as pd

from data.dataframe_provider import DataFrameMarketDataProvider


PORTFOLIO_HOLDING_COLUMNS = ["fund_id", "fund_name", "isin"]
UNIVERSE_COLUMNS = ["ticker", "data_id", "sector", "industry"]
PORTFOLIO_PRICE_COLUMNS = [
    "isin", "ticker", "data_id", "name", "threshold_pct", "date", "close",
]
MOMENTUM_PRICE_COLUMNS = ["data_id", "date", "value"]


def load_portfolio_isins(as_of: date | datetime) -> pd.DataFrame:
    """Return fund membership on `as_of`: fund_id, fund_name, isin.

    This is the only function the portfolio SQL integration has to implement.
    One row per (fund_id, isin); a security may belong to multiple funds.
    """
    # TODO: Run the colleague's internal portfolio SQL query here, for example:
    # frame = pd.read_sql(query, connection, params={"as_of": as_of})
    # return frame[["fund_id", "fund_name", "isin"]]
    return pd.DataFrame(columns=PORTFOLIO_HOLDING_COLUMNS)


def load_sp500_universe(as_of: date | datetime) -> pd.DataFrame:
    """Return the S&P 500 constituents and their classifications.

    Required columns:
        ticker, data_id
    Optional but recommended columns:
        sector, industry
    """
    # TODO: Load the internal LSEG/Datastream S&P 500 universe here.
    return pd.DataFrame(columns=UNIVERSE_COLUMNS)


def load_portfolio_prices(
    isins: Sequence[str], as_of: date | datetime,
) -> pd.DataFrame:
    """Resolve held ISINs and return at least six months of daily closes.

    Required long-format columns:
        isin, ticker, data_id, date, close
    Optional columns:
        name, threshold_pct

    Every requested ISIN must have exactly one ticker/data_id mapping. The
    mapping columns repeat on daily rows; the workflow derives the unique
    portfolio composition from them.
    """
    # TODO: Pass `isins` to the internal LSEG/Datastream price request here.
    return pd.DataFrame(columns=PORTFOLIO_PRICE_COLUMNS)


def load_momentum_prices(
    data_ids: Sequence[str], as_of: date | datetime,
) -> pd.DataFrame:
    """Return preferably 15 months of adjusted/return-index daily values.

    Required long-format columns:
        data_id, date, value
    """
    # TODO: Load S&P 500 adjusted prices or Datastream Return Index here.
    return pd.DataFrame(columns=MOMENTUM_PRICE_COLUMNS)


def build_internal_data_provider(
    as_of: date | datetime,
) -> tuple[list[dict], DataFrameMarketDataProvider]:
    """Assemble and validate the four loader outputs for the daily workflow."""
    holdings = load_portfolio_isins(as_of)
    if not isinstance(holdings, pd.DataFrame):
        raise ValueError("load_portfolio_isins() must return a DataFrame with fund_id, fund_name, isin")
    if holdings.empty:
        raise RuntimeError(
            "Internal fund/ISIN table is empty; implement "
            "load_portfolio_isins() in data/internal_data_source.py"
        )
    missing = set(PORTFOLIO_HOLDING_COLUMNS) - set(holdings.columns)
    if missing:
        raise ValueError(f"Fund holdings are missing required columns: {sorted(missing)}")
    holdings = holdings[PORTFOLIO_HOLDING_COLUMNS].copy()
    for column in PORTFOLIO_HOLDING_COLUMNS:
        if holdings[column].isna().any():
            raise ValueError(f"Fund holdings contain missing {column} values")
        holdings[column] = holdings[column].astype(str).str.strip()
        if holdings[column].eq("").any():
            raise ValueError(f"Fund holdings contain blank {column} values")
    holdings["isin"] = holdings["isin"].str.upper()
    if holdings.groupby("fund_id")["fund_name"].nunique().gt(1).any():
        raise ValueError("Each fund_id must map to exactly one fund_name")
    holdings = holdings.drop_duplicates(["fund_id", "isin"])
    portfolio_isins = holdings["isin"].drop_duplicates().tolist()
    universe = load_sp500_universe(as_of)
    if universe.empty:
        raise RuntimeError(
            "Internal S&P 500 universe DataFrame is empty; implement "
            "load_sp500_universe() in data/internal_data_source.py"
        )

    universe_data_ids = universe["data_id"].dropna().astype(str).tolist()
    portfolio_prices = load_portfolio_prices(portfolio_isins, as_of)
    momentum_prices = load_momentum_prices(universe_data_ids, as_of)
    if portfolio_prices.empty:
        raise RuntimeError(
            "Internal portfolio price DataFrame is empty; implement "
            "load_portfolio_prices() in data/internal_data_source.py"
        )
    if momentum_prices.empty:
        raise RuntimeError(
            "Internal momentum price DataFrame is empty; implement "
            "load_momentum_prices() in data/internal_data_source.py"
        )

    required_portfolio_columns = {"isin", "ticker", "data_id", "date", "close"}
    missing_columns = required_portfolio_columns - set(portfolio_prices.columns)
    if missing_columns:
        raise ValueError(
            "Internal portfolio price DataFrame is missing required columns: "
            f"{sorted(missing_columns)}"
        )
    resolved = portfolio_prices.copy()
    resolved["isin"] = resolved["isin"].astype(str).str.strip().str.upper()
    resolved = resolved[resolved["isin"].isin(portfolio_isins)].copy()
    returned_isins = set(resolved["isin"])
    missing_isins = [isin for isin in portfolio_isins if isin not in returned_isins]
    if missing_isins:
        raise ValueError(
            f"No portfolio price/reference data returned for ISINs: {missing_isins}"
        )
    if "name" not in resolved:
        resolved["name"] = resolved["ticker"]
    else:
        resolved["name"] = resolved["name"].fillna(resolved["ticker"])
    if "threshold_pct" not in resolved:
        resolved["threshold_pct"] = 2.0
    else:
        resolved["threshold_pct"] = resolved["threshold_pct"].fillna(2.0)
    portfolio = resolved[[
        "isin", "ticker", "data_id", "name", "threshold_pct",
    ]].drop_duplicates()
    if portfolio["isin"].duplicated().any():
        raise ValueError("A portfolio ISIN maps to multiple ticker/data_id values")
    if portfolio["ticker"].duplicated().any():
        raise ValueError("Multiple portfolio ISINs map to the same ticker")

    provider = DataFrameMarketDataProvider(
        universe=universe,
        portfolio_prices=resolved,
        momentum_prices=momentum_prices,
    )
    fund_portfolio = holdings.merge(portfolio, on="isin", how="left", validate="many_to_one")
    return fund_portfolio.to_dict("records"), provider

#!/usr/bin/env python3
"""
Scan OHLCV CSV files for RPS watchlists and pocket pivot candidates.

Input:
  A directory of CSV files, one symbol per file. Required columns:
  date, open, high, low, close, volume
  If --timeframe is 4h, provide 4h OHLCV files; RPS periods are counted in bars.
  Put --output outside the input directory so generated CSV files are not read as symbols.

Example:
  python3 tools/rps_pocket_pivot_scanner.py \
    --input data/us_ohlcv \
    --market us \
    --mode watchlist \
    --output /tmp/rps_pp_us.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


US_PERIODS = (30, 50, 120, 250)
CRYPTO_PERIODS = (30, 90, 180)
MACRO_PERIODS = (20, 60, 120)


def load_ohlcv(input_dir: Path) -> pd.DataFrame:
    frames = []
    for csv_path in sorted(input_dir.glob("*.csv")):
        symbol = csv_path.stem.upper()
        df = pd.read_csv(csv_path)
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns.str.lower())
        if missing:
            raise ValueError(f"{csv_path} missing columns: {sorted(missing)}")

        df.columns = [c.lower() for c in df.columns]
        df = df[["date", "open", "high", "low", "close", "volume"]].copy()
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df["symbol"] = symbol
        frames.append(df)

    if not frames:
        raise ValueError(f"No CSV files found in {input_dir}")

    data = pd.concat(frames, ignore_index=True)
    data = data.sort_values(["symbol", "date"])
    return data


def add_group_indicators(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    g = data.groupby("symbol", group_keys=False)

    for window in (10, 20, 50, 120, 150, 200):
        data[f"ma{window}"] = g["close"].transform(
            lambda s: s.rolling(window, min_periods=window).mean()
        )

    data["prev_close"] = g["close"].shift(1)
    data["up_day"] = data["close"] > data["prev_close"]
    data["down_day"] = data["close"] < data["prev_close"]
    data["down_volume"] = data["volume"].where(data["down_day"])
    data["down_volume_max_10"] = g["down_volume"].transform(
        lambda s: s.shift(1).rolling(10, min_periods=1).max()
    )
    data["volume_signature"] = data["volume"] > data["down_volume_max_10"]

    data["highest_20_prev"] = g["high"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=20).max()
    )
    data["highest_252"] = g["high"].transform(
        lambda s: s.rolling(252, min_periods=120).max()
    )
    data["ma200_20_ago"] = g["ma200"].shift(20)
    return data


def add_rps(data: pd.DataFrame, periods: tuple[int, ...]) -> pd.DataFrame:
    data = data.copy()
    close = data.pivot(index="date", columns="symbol", values="close").sort_index()

    for period in periods:
        returns = close / close.shift(period) - 1
        rps = returns.rank(axis=1, pct=True) * 100
        long_rps = (
            rps.stack()
            .rename(f"rps{period}")
            .reset_index()
            .rename(columns={"level_1": "symbol"})
        )
        data = data.merge(long_rps, on=["date", "symbol"], how="left")

    rps_cols = [f"rps{p}" for p in periods]
    data["rps_max"] = data[rps_cols].max(axis=1)
    data["rps_short"] = data[f"rps{periods[0]}"]
    return data


def add_us_screen_flags(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    structure = (
        (data["close"] > data["ma50"])
        & (data["close"] > data["ma150"])
        & (data["close"] > data["ma200"])
        & (data["ma200"] >= data["ma200_20_ago"] * 0.995)
        & (data["close"] >= data["highest_252"] * 0.85)
    )
    price_location = (
        ((data["low"] <= data["ma10"] * 1.03) & (data["close"] > data["ma10"]))
        | ((data["low"] <= data["ma20"] * 1.03) & (data["close"] > data["ma20"]))
        | (data["close"] >= data["highest_20_prev"])
    )
    short_rps = data["rps50"] if "rps50" in data.columns else data["rps_short"]
    data["watchlist"] = data["rps_max"] >= 90
    data["core_watchlist"] = (data["rps_max"] >= 95) & (short_rps >= 90)
    data["strong_trend"] = structure
    data["pocket_pivot"] = (
        data["watchlist"]
        & data["strong_trend"]
        & data["up_day"]
        & data["volume_signature"]
        & price_location
        & (data["close"] / data["ma10"] <= 1.08)
    )
    data["low_price_risk"] = data["close"] < 5
    data["tier"] = "C"
    data.loc[data["watchlist"] & data["strong_trend"], "tier"] = "B"
    data.loc[data["core_watchlist"] & data["strong_trend"] & data["pocket_pivot"], "tier"] = "A"
    return data


def add_crypto_screen_flags(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    trend_location = (data["close"] > data["ma20"]) & (data["close"] > data["ma50"])
    data["watchlist"] = (data["rps_max"] >= 90) & (data["rps_short"] >= 80)
    data["core_watchlist"] = (data["rps_max"] >= 95) & (data["rps_short"] >= 90)
    data["strong_trend"] = trend_location
    data["pocket_pivot"] = (
        data["watchlist"]
        & (data["rps_short"] >= 80)
        & data["up_day"]
        & data["volume_signature"]
        & trend_location
        & (data["close"] / data["ma20"] <= 1.15)
    )
    data["low_price_risk"] = False
    data["tier"] = "C"
    data.loc[data["watchlist"] & data["strong_trend"], "tier"] = "B"
    data.loc[data["core_watchlist"] & data["strong_trend"] & data["pocket_pivot"], "tier"] = "A"
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--market", choices=("us", "crypto"), default="us")
    parser.add_argument(
        "--timeframe",
        choices=("1d", "4h"),
        default="1d",
        help="Input bar interval. For 4h crypto scans, RPS30 means 30 four-hour bars.",
    )
    parser.add_argument(
        "--mode",
        choices=("watchlist", "signals"),
        default="watchlist",
        help="watchlist outputs all RPS-qualified rows; signals outputs only pocket pivots.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    periods = US_PERIODS if args.market == "us" else CRYPTO_PERIODS
    data = load_ohlcv(args.input)
    data = add_group_indicators(data)
    data = add_rps(data, periods)
    if args.market == "us":
        data["rps_short"] = data["rps50"]

    screened = add_us_screen_flags(data) if args.market == "us" else add_crypto_screen_flags(data)
    if args.mode == "signals":
        candidates = screened.loc[screened["pocket_pivot"]].copy()
    else:
        candidates = screened.loc[screened["watchlist"]].copy()
    candidates["timeframe"] = args.timeframe

    keep = [
        "date",
        "timeframe",
        "symbol",
        "tier",
        "close",
        "volume",
        "rps_max",
        "rps_short",
        *[f"rps{p}" for p in periods],
        "ma10",
        "ma20",
        "ma50",
        "watchlist",
        "core_watchlist",
        "strong_trend",
        "pocket_pivot",
        "low_price_risk",
        "volume_signature",
    ]
    candidates = candidates[keep].sort_values(
        ["date", "tier", "rps_max"],
        ascending=[False, True, False],
    )

    if args.output:
        candidates.to_csv(args.output, index=False)
    else:
        print(candidates.to_string(index=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Daily RPS + pocket pivot updater.

This script downloads/updates OHLCV files, then writes two scan tables:
watchlist and signals. It intentionally uses stdlib networking so the only
runtime dependency is pandas, already available in the bundled Codex Python.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import rps_pocket_pivot_scanner as scanner


BINANCE_API = "https://api.binance.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"
POLYGON_API = "https://api.polygon.io"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
SPTM_HOLDINGS_XLSX_URL = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-sptm.xlsx"
IWV_HOLDINGS_CSV_URL = "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf/1467271812596.ajax?fileType=csv&fileName=IWV_holdings&dataType=fund"

DEFAULT_US_SYMBOLS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "GOOG",
    "AVGO",
    "TSLA",
    "AMD",
    "NFLX",
    "PLTR",
    "COIN",
    "MSTR",
    "TSM",
    "ASML",
    "ARM",
    "SMCI",
    "CRWD",
    "NET",
    "QQQ",
    "SPY",
    "IWM",
]

MACRO_ASSETS = [
    {"symbol": "QQQ", "name": "Nasdaq 100", "group": "US Tech Growth", "source": "polygon"},
    {"symbol": "SPY", "name": "S&P 500", "group": "US Equities", "source": "polygon"},
    {"symbol": "IWM", "name": "Russell 2000", "group": "US Small Caps", "source": "polygon"},
    {"symbol": "GLD", "name": "Gold", "group": "Hard Assets", "source": "polygon"},
    {"symbol": "DBC", "name": "Broad Commodities", "group": "Commodities", "source": "polygon"},
    {"symbol": "TLT", "name": "20+ Year Treasuries", "group": "Duration / Bonds", "source": "polygon"},
    {"symbol": "HYG", "name": "High Yield Credit", "group": "Credit Risk", "source": "polygon"},
    {"symbol": "UUP", "name": "US Dollar Index", "group": "US Dollar", "source": "polygon"},
    {"symbol": "EEM", "name": "Emerging Markets", "group": "Non-US Equities", "source": "polygon"},
    {"symbol": "BTCUSDT", "name": "Bitcoin", "group": "Crypto", "source": "binance"},
]


def macro_asset_symbols() -> list[str]:
    return [asset["symbol"] for asset in MACRO_ASSETS]


def macro_asset_lookup() -> dict[str, dict[str, str]]:
    return {asset["symbol"]: asset for asset in MACRO_ASSETS}


def http_text(url: str, timeout: int = 30, attempts: int = 3) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "rps-daily-runner/1.0"})
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    raise last_error


def http_json(url: str, timeout: int = 30):
    return json.loads(http_text(url, timeout=timeout))


def load_env_values(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def polygon_api_key(env_file: Path | None) -> str | None:
    values = load_env_values(env_file)
    return values.get("polygon_key") or values.get("POLYGON_API_KEY") or values.get("POLYGON_KEY")


def append_ohlcv(path: Path, incoming: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["date", "open", "high", "low", "close", "volume"]
    incoming = incoming[columns].copy()
    incoming["date"] = pd.to_datetime(incoming["date"], utc=True)

    if path.exists():
        existing = pd.read_csv(path)
        existing = existing[columns].copy()
        existing["date"] = pd.to_datetime(existing["date"], utc=True)
        merged = pd.concat([existing, incoming], ignore_index=True)
    else:
        merged = incoming

    merged = (
        merged.sort_values("date")
        .assign(_market_date=lambda frame: frame["date"].dt.strftime("%Y-%m-%d"))
        .drop_duplicates(subset=["_market_date"], keep="last")
        .assign(date=lambda frame: pd.to_datetime(frame["_market_date"], utc=True))
        .drop(columns=["_market_date"])
        .sort_values("date")
    )
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    merged.to_csv(path, index=False)


def latest_timestamp_ms(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, usecols=["date"])
    except Exception:
        return None
    if df.empty:
        return None
    last = pd.to_datetime(df["date"].iloc[-1], utc=True)
    return int(last.timestamp() * 1000)


def history_bar_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open(newline="") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    except Exception:
        return 0


def us_symbols_needing_history(data_dir: Path, symbols: Iterable[str], min_bars: int = 250) -> list[str]:
    target_dir = data_dir / "us_1d"
    needed = []
    for symbol in sorted({symbol.upper() for symbol in symbols}):
        if history_bar_count(target_dir / f"{symbol}.csv") < min_bars:
            needed.append(symbol)
    return needed


def interval_to_millis(interval: str) -> int:
    if interval == "1d":
        return 24 * 60 * 60 * 1000
    if interval == "4h":
        return 4 * 60 * 60 * 1000
    raise ValueError(f"Unsupported interval: {interval}")


def get_binance_usdt_symbols() -> set[str]:
    tickers = http_json(f"{BINANCE_API}/api/v3/ticker/24hr", timeout=60)
    symbols = set()
    for item in tickers:
        symbol = item.get("symbol", "")
        if symbol.endswith("USDT"):
            symbols.add(symbol)
    return symbols


def coingecko_top_symbols(limit: int) -> list[str]:
    per_page = min(250, max(limit, 1))
    params = urllib.parse.urlencode(
        {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": 1,
            "sparkline": "false",
        }
    )
    markets = http_json(f"{COINGECKO_API}/coins/markets?{params}")
    symbols = []
    for coin in markets[:limit]:
        sym = str(coin.get("symbol", "")).upper()
        if sym and sym not in {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE"}:
            symbols.append(f"{sym}USDT")
    return symbols


def binance_top_quote_volume_symbols(limit: int) -> list[str]:
    tickers = http_json(f"{BINANCE_API}/api/v3/ticker/24hr", timeout=60)
    rows = []
    for ticker in tickers:
        symbol = ticker.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol.removesuffix("USDT")
        if base in {"USDC", "FDUSD", "TUSD", "DAI", "USDE"}:
            continue
        rows.append((float(ticker.get("quoteVolume", 0) or 0), symbol))
    rows.sort(reverse=True)
    return [symbol for _, symbol in rows[:limit]]


def get_crypto_symbols(limit: int) -> list[str]:
    binance_symbols = get_binance_usdt_symbols()
    try:
        mapped = [symbol for symbol in coingecko_top_symbols(limit * 2) if symbol in binance_symbols]
        if mapped:
            return mapped[:limit]
    except Exception:
        pass
    return [symbol for symbol in binance_top_quote_volume_symbols(limit) if symbol in binance_symbols]


def parse_sptm_holdings(holdings: pd.DataFrame) -> list[str]:
    ticker_column = None
    for column in holdings.columns:
        if str(column).strip().lower() in {"ticker", "ticker symbol", "symbol"}:
            ticker_column = column
            break
    if ticker_column is None:
        raise ValueError("SPTM holdings file does not contain a ticker column")

    symbols = []
    seen = set()
    for raw in holdings[ticker_column].dropna().tolist():
        symbol = str(raw).strip().upper()
        if not symbol or symbol in {"-", "CASH", "N/A", "NAN"}:
            continue
        if any(marker in symbol for marker in (" ", "/", "$", "^")):
            continue
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def fetch_sptm_symbols(cache_path: Path | None = None) -> list[str]:
    if cache_path and cache_path.exists():
        cached = pd.read_csv(cache_path)
        return parse_sptm_holdings(cached)

    request = urllib.request.Request(SPTM_HOLDINGS_XLSX_URL, headers={"User-Agent": "rps-daily-runner/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    holdings = pd.read_excel(io.BytesIO(content), skiprows=4)
    symbols = parse_sptm_holdings(holdings)
    if cache_path and max_symbols is None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"Ticker": symbols}).to_csv(cache_path, index=False)
    return symbols


def parse_ishares_holdings_csv(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    header_index = None
    for index, line in enumerate(lines):
        lowered = line.lower()
        if "ticker" in lowered and ("name" in lowered or "asset class" in lowered):
            header_index = index
            break
    if header_index is None:
        raise ValueError("iShares holdings CSV did not contain a holdings header")
    holdings = pd.read_csv(io.StringIO("\n".join(lines[header_index:])))
    return parse_sptm_holdings(holdings)


def fetch_iwv_symbols(cache_path: Path | None = None) -> list[str]:
    if cache_path and cache_path.exists():
        cached = pd.read_csv(cache_path)
        return parse_sptm_holdings(cached)
    text = http_text(IWV_HOLDINGS_CSV_URL, timeout=60)
    if "<!DOCTYPE html" in text[:200]:
        raise ValueError("iShares IWV holdings endpoint returned HTML instead of CSV")
    symbols = parse_ishares_holdings_csv(text)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"Ticker": symbols}).to_csv(cache_path, index=False)
    return symbols


def fetch_polygon_common_stock_symbols(
    api_key: str,
    max_symbols: int | None = None,
    cache_path: Path | None = None,
    request_sleep: float = 12.0,
) -> list[str]:
    if cache_path and cache_path.exists():
        cached = pd.read_csv(cache_path)
        symbols = parse_sptm_holdings(cached)
        return symbols[:max_symbols] if max_symbols else symbols

    symbols = []
    url = (
        f"{POLYGON_API}/v3/reference/tickers?"
        + urllib.parse.urlencode(
            {
                "market": "stocks",
                "locale": "us",
                "type": "CS",
                "active": "true",
                "limit": 1000,
                "apiKey": api_key,
            }
        )
    )
    while url:
        while True:
            try:
                payload = http_json(url, timeout=60)
                break
            except urllib.error.HTTPError as error:
                if error.code == 429:
                    wait_seconds = max(request_sleep, 60)
                    print(f"polygon common universe rate_limited, waiting {wait_seconds:.0f}s", flush=True)
                    time.sleep(wait_seconds)
                    continue
                raise
        for item in payload.get("results") or []:
            ticker = str(item.get("ticker", "")).upper()
            if ticker and not any(marker in ticker for marker in ("$", "^", "/")):
                symbols.append(ticker)
                if max_symbols and len(symbols) >= max_symbols:
                    return symbols
        next_url = payload.get("next_url")
        url = f"{next_url}&apiKey={api_key}" if next_url else ""
        if url and request_sleep > 0:
            time.sleep(request_sleep)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"Ticker": symbols}).to_csv(cache_path, index=False)
    return symbols


def fetch_binance_klines(symbol: str, interval: str, start_ms: int | None, lookback_days: int) -> pd.DataFrame:
    interval_ms = interval_to_millis(interval)
    if start_ms is None:
        start_ms = int((datetime.now(UTC) - timedelta(days=lookback_days)).timestamp() * 1000)
    else:
        start_ms = max(0, start_ms - interval_ms * 3)

    rows = []
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    cursor = start_ms
    while cursor < end_ms:
        params = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "limit": 1000,
            }
        )
        batch = http_json(f"{BINANCE_API}/api/v3/klines?{params}")
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + interval_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < 1000:
            break
        time.sleep(0.05)

    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    parsed = []
    for row in rows:
        close_time = int(row[6])
        if close_time > now_ms:
            continue
        parsed.append(
            {
                "date": datetime.fromtimestamp(int(row[0]) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[7]),  # quote asset volume, usually USDT
            }
        )
    return pd.DataFrame(parsed)


def update_crypto_data(data_dir: Path, timeframe: str, limit: int, min_quote_volume_30d: float, lookback_days: int) -> list[str]:
    interval = "1d" if timeframe == "1d" else "4h"
    symbols = get_crypto_symbols(limit)
    kept = []
    target_dir = data_dir / f"crypto_{timeframe}"
    target_dir.mkdir(parents=True, exist_ok=True)

    for index, symbol in enumerate(symbols, start=1):
        path = target_dir / f"{symbol}.csv"
        incoming = fetch_binance_klines(symbol, interval, latest_timestamp_ms(path), lookback_days)
        if incoming.empty:
            continue
        append_ohlcv(path, incoming)
        df = pd.read_csv(path)
        if len(df) >= 30 and df["volume"].tail(30).mean() >= min_quote_volume_30d:
            kept.append(symbol)
        elif path.exists():
            path.unlink()
        print(f"crypto {timeframe} {index}/{len(symbols)} {symbol}", flush=True)

    return kept


def parse_nasdaq_symbol_file(text: str, symbol_column: str) -> list[str]:
    rows = csv.DictReader(text.splitlines(), delimiter="|")
    symbols = []
    for row in rows:
        symbol = row.get(symbol_column, "").strip()
        if not symbol or symbol == "File Creation Time":
            continue
        if row.get("Test Issue", "N") != "N":
            continue
        if row.get("ETF", "N") == "Y":
            continue
        if any(marker in symbol for marker in ("$", "^", "/")):
            continue
        symbols.append(symbol)
    return symbols


def fetch_us_universe(max_symbols: int | None) -> list[str]:
    symbols = []
    try:
        symbols.extend(parse_nasdaq_symbol_file(http_text(NASDAQ_LISTED_URL), "Symbol"))
        symbols.extend(parse_nasdaq_symbol_file(http_text(OTHER_LISTED_URL), "ACT Symbol"))
    except Exception:
        symbols = DEFAULT_US_SYMBOLS.copy()

    deduped = []
    seen = set()
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            deduped.append(symbol)
    return deduped[:max_symbols] if max_symbols else deduped


def load_us_symbols(
    symbols_file: Path | None,
    fetch_universe: bool,
    max_symbols: int | None,
    universe: str = "config",
    data_dir: Path | None = None,
    polygon_key: str | None = None,
    request_sleep: float = 12.0,
) -> list[str]:
    cache_root = (data_dir or Path("data/rps_pp")) / "universe"
    if universe == "iwv":
        try:
            symbols = fetch_iwv_symbols(cache_root / "iwv_holdings.csv")
        except Exception as error:
            if not polygon_key:
                raise
            print(f"iwv holdings unavailable, falling back to polygon-common: {error}", flush=True)
            symbols = fetch_polygon_common_stock_symbols(
                polygon_key,
                max_symbols=max_symbols,
                cache_path=cache_root / "polygon_common_stocks.csv",
                request_sleep=request_sleep,
            )
        return symbols[:max_symbols] if max_symbols else symbols
    if universe == "polygon-common":
        if not polygon_key:
            raise ValueError("polygon-common universe requires polygon_key, POLYGON_API_KEY, or POLYGON_KEY in the env file.")
        return fetch_polygon_common_stock_symbols(
            polygon_key,
            max_symbols=max_symbols,
            cache_path=cache_root / "polygon_common_stocks.csv",
            request_sleep=request_sleep,
        )
    if universe == "sptm":
        cache_path = cache_root / "sptm_holdings.csv"
        symbols = fetch_sptm_symbols(cache_path)
        return symbols[:max_symbols] if max_symbols else symbols
    if symbols_file and symbols_file.exists():
        symbols = [
            line.strip().upper()
            for line in symbols_file.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return symbols[:max_symbols] if max_symbols else symbols
    if fetch_universe:
        return fetch_us_universe(max_symbols)
    return DEFAULT_US_SYMBOLS[:max_symbols] if max_symbols else DEFAULT_US_SYMBOLS.copy()


def stooq_symbol(symbol: str) -> str:
    return symbol.lower().replace("-", ".") + ".us"


def fetch_yahoo_daily(symbol: str, start_date: str | None, lookback_days: int) -> pd.DataFrame:
    if start_date is None:
        start = datetime.now(UTC) - timedelta(days=lookback_days)
    else:
        start = pd.to_datetime(start_date, utc=True) - pd.Timedelta(days=7)
    period1 = int(start.timestamp())
    period2 = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    params = urllib.parse.urlencode(
        {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}"
    payload = http_json(url)
    result = payload.get("chart", {}).get("result") or []
    if not result:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    item = result[0]
    timestamps = item.get("timestamp") or []
    quote = (item.get("indicators", {}).get("quote") or [{}])[0]
    adjclose = (item.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
    rows = []
    for index, ts in enumerate(timestamps):
        try:
            open_price = quote["open"][index]
            high = quote["high"][index]
            low = quote["low"][index]
            close = quote["close"][index]
            volume = quote["volume"][index]
        except (KeyError, IndexError):
            continue
        if None in (open_price, high, low, close, volume):
            continue
        adjusted_close = adjclose[index] if index < len(adjclose) and adjclose[index] else close
        ratio = adjusted_close / close if close else 1
        rows.append(
            {
                "date": datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": float(open_price) * ratio,
                "high": float(high) * ratio,
                "low": float(low) * ratio,
                "close": float(adjusted_close),
                "volume": float(volume),
            }
        )
    return pd.DataFrame(rows)


def polygon_aggs_to_ohlcv(payload: dict) -> pd.DataFrame:
    rows = []
    for item in payload.get("results") or []:
        required = ("t", "o", "h", "l", "c", "v")
        if not all(key in item and item[key] is not None for key in required):
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(int(item["t"]) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": float(item["o"]),
                "high": float(item["h"]),
                "low": float(item["l"]),
                "close": float(item["c"]),
                "volume": float(item["v"]),
            }
        )
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def grouped_daily_to_symbol_frames(payload: dict, universe: set[str]) -> dict[str, pd.DataFrame]:
    rows_by_symbol: dict[str, list[dict]] = {}
    for item in payload.get("results") or []:
        symbol = str(item.get("T", "")).upper()
        if symbol not in universe:
            continue
        required = ("t", "o", "h", "l", "c", "v")
        if not all(key in item and item[key] is not None for key in required):
            continue
        rows_by_symbol.setdefault(symbol, []).append(
            {
                "date": datetime.fromtimestamp(int(item["t"]) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": float(item["o"]),
                "high": float(item["h"]),
                "low": float(item["l"]),
                "close": float(item["c"]),
                "volume": float(item["v"]),
            }
        )
    return {
        symbol: pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        for symbol, rows in rows_by_symbol.items()
    }


def fetch_polygon_grouped_daily(date: datetime, api_key: str, adjusted: bool = True) -> dict:
    params = urllib.parse.urlencode({"adjusted": str(adjusted).lower(), "apiKey": api_key})
    url = (
        f"{POLYGON_API}/v2/aggs/grouped/locale/us/market/stocks/"
        f"{date.strftime('%Y-%m-%d')}?{params}"
    )
    return http_json(url, timeout=60)


def fetch_polygon_daily(symbol: str, start_date: str | None, lookback_days: int, api_key: str) -> pd.DataFrame:
    if start_date is None:
        start = datetime.now(UTC) - timedelta(days=lookback_days)
    else:
        start = pd.to_datetime(start_date, utc=True) - pd.Timedelta(days=7)
    end = datetime.now(UTC) + timedelta(days=1)
    params = urllib.parse.urlencode(
        {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": api_key,
        }
    )
    url = (
        f"{POLYGON_API}/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/1/day/"
        f"{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}?{params}"
    )
    return polygon_aggs_to_ohlcv(http_json(url, timeout=60))


def fetch_stooq_daily(symbol: str, start_date: str | None, lookback_days: int) -> pd.DataFrame:
    if start_date is None:
        start = datetime.now(UTC) - timedelta(days=lookback_days)
        d1 = start.strftime("%Y%m%d")
    else:
        start = pd.to_datetime(start_date, utc=True) - pd.Timedelta(days=7)
        d1 = start.strftime("%Y%m%d")
    params = urllib.parse.urlencode({"s": stooq_symbol(symbol), "i": "d", "d1": d1})
    text = http_text(f"https://stooq.com/q/d/l/?{params}")
    if "No data" in text or not text.strip():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    rows = pd.read_csv(io.StringIO(text))
    if rows.empty or "Date" not in rows.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    rows = rows.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    rows["date"] = pd.to_datetime(rows["date"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return rows[["date", "open", "high", "low", "close", "volume"]]


def update_us_data(
    data_dir: Path,
    symbols: Iterable[str],
    lookback_days: int,
    provider: str,
    polygon_key: str | None,
    request_sleep: float,
    max_retries: int = 3,
) -> list[str]:
    if provider == "polygon" and not polygon_key:
        raise ValueError("Polygon provider requires polygon_key, POLYGON_API_KEY, or POLYGON_KEY in the env file.")

    target_dir = data_dir / "us_1d"
    target_dir.mkdir(parents=True, exist_ok=True)
    symbols = list(symbols)
    kept = []
    for index, symbol in enumerate(symbols, start=1):
        path = target_dir / f"{symbol}.csv"
        last_ms = latest_timestamp_ms(path)
        start_date = datetime.fromtimestamp(last_ms / 1000, UTC).strftime("%Y-%m-%d") if last_ms else None
        incoming = None
        for attempt in range(1, max_retries + 1):
            try:
                if provider == "polygon":
                    incoming = fetch_polygon_daily(symbol, start_date, lookback_days, polygon_key or "")
                elif provider == "yahoo":
                    incoming = fetch_yahoo_daily(symbol, start_date, lookback_days)
                else:
                    raise ValueError(f"Unsupported US data provider: {provider}")
                break
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < max_retries:
                    wait_seconds = max(request_sleep, 60)
                    print(
                        f"us 1d {index}/{len(symbols)} {symbol} rate_limited retry {attempt}/{max_retries}",
                        flush=True,
                    )
                    time.sleep(wait_seconds)
                    continue
                raise
        if incoming is None:
            continue
        if incoming.empty:
            continue
        append_ohlcv(path, incoming)
        kept.append(symbol)
        print(f"us 1d {index}/{len(symbols)} {symbol} {provider}", flush=True)
        if request_sleep > 0:
            time.sleep(request_sleep)
    return kept


def update_macro_data(
    data_dir: Path,
    lookback_days: int,
    polygon_key: str | None,
    request_sleep: float,
    max_retries: int = 3,
) -> list[str]:
    if any(asset["source"] == "polygon" for asset in MACRO_ASSETS) and not polygon_key:
        raise ValueError("Macro ETF data requires polygon_key, POLYGON_API_KEY, or POLYGON_KEY in the env file.")

    target_dir = data_dir / "macro_1d"
    target_dir.mkdir(parents=True, exist_ok=True)
    kept = []
    for index, asset in enumerate(MACRO_ASSETS, start=1):
        symbol = asset["symbol"]
        path = target_dir / f"{symbol}.csv"
        last_ms = latest_timestamp_ms(path)
        start_date = datetime.fromtimestamp(last_ms / 1000, UTC).strftime("%Y-%m-%d") if last_ms else None
        incoming = None
        for attempt in range(1, max_retries + 1):
            try:
                if asset["source"] == "binance":
                    incoming = fetch_binance_klines(symbol, "1d", last_ms, lookback_days)
                elif asset["source"] == "polygon":
                    incoming = fetch_polygon_daily(symbol, start_date, lookback_days, polygon_key or "")
                else:
                    raise ValueError(f"Unsupported macro data source: {asset['source']}")
                break
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < max_retries:
                    wait_seconds = max(request_sleep, 60)
                    print(
                        f"macro 1d {index}/{len(MACRO_ASSETS)} {symbol} rate_limited retry {attempt}/{max_retries}",
                        flush=True,
                    )
                    time.sleep(wait_seconds)
                    continue
                raise
        if incoming is None or incoming.empty:
            continue
        append_ohlcv(path, incoming)
        kept.append(symbol)
        print(f"macro 1d {index}/{len(MACRO_ASSETS)} {symbol} {asset['source']}", flush=True)
        if asset["source"] == "polygon" and request_sleep > 0:
            time.sleep(request_sleep)
    return kept


def latest_universe_date(data_dir: Path) -> datetime | None:
    dates = []
    for csv_path in data_dir.glob("*.csv"):
        last_ms = latest_timestamp_ms(csv_path)
        if last_ms:
            dates.append(datetime.fromtimestamp(last_ms / 1000, UTC))
    return max(dates) if dates else None


def grouped_backfill_start(
    data_dir: Path,
    symbols: Iterable[str],
    lookback_days: int,
    now: datetime,
    force_backfill: bool = False,
) -> datetime:
    full_start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=lookback_days)
    if force_backfill:
        return full_start

    symbol_set = {symbol.upper() for symbol in symbols}
    existing_count = sum(1 for symbol in symbol_set if (data_dir / f"{symbol}.csv").exists())
    minimum_broad_coverage = min(100, max(1, int(len(symbol_set) * 0.2)))
    if existing_count < minimum_broad_coverage:
        return full_start

    latest = latest_universe_date(data_dir)
    if latest is None:
        return full_start
    return latest - timedelta(days=7)


def date_range(start: datetime, end: datetime) -> list[datetime]:
    days = []
    cursor = datetime(start.year, start.month, start.day, tzinfo=UTC)
    stop = datetime(end.year, end.month, end.day, tzinfo=UTC)
    while cursor <= stop:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def market_date_range(start: datetime, end: datetime) -> list[datetime]:
    return [day for day in date_range(start, end) if day.weekday() < 5]


def latest_polygon_grouped_available_date(now: datetime, delay_hours: int = 8) -> datetime:
    eastern_now = now.astimezone(ZoneInfo("America/New_York"))
    candidate = (eastern_now - timedelta(hours=16 + delay_hours)).date()
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return datetime(candidate.year, candidate.month, candidate.day, tzinfo=UTC)


def update_us_grouped_data(
    data_dir: Path,
    symbols: Iterable[str],
    lookback_days: int,
    polygon_key: str | None,
    request_sleep: float,
    force_backfill: bool = False,
    max_retries: int = 3,
) -> list[str]:
    if not polygon_key:
        raise ValueError("Polygon grouped provider requires polygon_key, POLYGON_API_KEY, or POLYGON_KEY in the env file.")

    target_dir = data_dir / "us_1d"
    target_dir.mkdir(parents=True, exist_ok=True)
    symbols = sorted({symbol.upper() for symbol in symbols})
    universe = set(symbols)

    now = datetime.now(UTC)
    start = grouped_backfill_start(target_dir, symbols, lookback_days, now, force_backfill=force_backfill)
    end = latest_polygon_grouped_available_date(now)

    rows_by_symbol: dict[str, list[pd.DataFrame]] = {}
    days = market_date_range(start, end)
    for index, day in enumerate(days, start=1):
        payload = None
        for attempt in range(1, max_retries + 1):
            try:
                payload = fetch_polygon_grouped_daily(day, polygon_key)
                break
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < max_retries:
                    wait_seconds = max(request_sleep, 60)
                    print(
                        f"us grouped {index}/{len(days)} {day.strftime('%Y-%m-%d')} rate_limited retry {attempt}/{max_retries}",
                        flush=True,
                    )
                    time.sleep(wait_seconds)
                    continue
                if error.code in {403, 404}:
                    print(
                        f"us grouped {index}/{len(days)} {day.strftime('%Y-%m-%d')} unavailable http={error.code}",
                        flush=True,
                    )
                    payload = {"results": []}
                    break
                raise
        if payload is None:
            continue
        frames = grouped_daily_to_symbol_frames(payload, universe)
        for symbol, frame in frames.items():
            if not frame.empty:
                rows_by_symbol.setdefault(symbol, []).append(frame)
        print(
            f"us grouped {index}/{len(days)} {day.strftime('%Y-%m-%d')} rows={sum(len(frame) for frame in frames.values())}",
            flush=True,
        )
        if request_sleep > 0:
            time.sleep(request_sleep)

    kept = []
    for symbol, frames in rows_by_symbol.items():
        combined = pd.concat(frames, ignore_index=True)
        append_ohlcv(target_dir / f"{symbol}.csv", combined)
        kept.append(symbol)
    return sorted(kept)


def add_macro_ranking_flags(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    lookup = macro_asset_lookup()
    data["asset_name"] = data["symbol"].map(lambda symbol: lookup.get(symbol, {}).get("name", symbol))
    data["macro_group"] = data["symbol"].map(lambda symbol: lookup.get(symbol, {}).get("group", "Other"))
    data["watchlist"] = data["rps_max"].notna()
    data["core_watchlist"] = data["rps_max"] >= 80
    data["strong_trend"] = data["close"] > data["ma50"]
    data["pocket_pivot"] = False
    data["low_price_risk"] = False
    data["volume_signature"] = False
    data["tier"] = "C"
    data.loc[data["rps_max"] >= 60, "tier"] = "B"
    data.loc[data["rps_max"] >= 80, "tier"] = "A"
    return data


def filter_macro_common_dates(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return data
    symbol_count = data["symbol"].nunique()
    common_dates = data.groupby("date")["symbol"].nunique()
    common_dates = common_dates.loc[common_dates >= symbol_count].index
    if len(common_dates) == 0:
        return data
    return data.loc[data["date"].isin(common_dates)].copy()


def run_scans(input_dir: Path, output_dir: Path, market: str, timeframe: str, prefix: str) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "watchlist": output_dir / f"{prefix}_watchlist.csv",
        "signals": output_dir / f"{prefix}_signals.csv",
    }
    for mode, path in outputs.items():
        data = scanner.load_ohlcv(input_dir)
        if market == "macro":
            data = filter_macro_common_dates(data)
        if market == "us":
            data = filter_us_tradable_universe(data)
        data = scanner.add_group_indicators(data)
        if market == "us":
            periods = scanner.US_PERIODS
        elif market == "crypto":
            periods = scanner.CRYPTO_PERIODS
        elif market == "macro":
            periods = scanner.MACRO_PERIODS
        else:
            raise ValueError(f"Unsupported scan market: {market}")
        data = scanner.add_rps(data, periods)
        if market == "us":
            data["rps_short"] = data["rps50"]
        if market == "us":
            screened = scanner.add_us_screen_flags(data)
        elif market == "crypto":
            screened = scanner.add_crypto_screen_flags(data)
        else:
            screened = add_macro_ranking_flags(data)
        if not screened.empty:
            if market == "macro":
                symbol_count = screened["symbol"].nunique()
                date_counts = screened.groupby("date")["symbol"].nunique()
                complete_dates = date_counts.loc[date_counts >= symbol_count]
                latest_date = complete_dates.index.max() if not complete_dates.empty else screened["date"].max()
            else:
                latest_date = screened["date"].max()
            screened = screened.loc[screened["date"] == latest_date].copy()
        if market == "macro":
            candidates = screened.loc[screened["watchlist"]].copy()
        else:
            candidates = screened.loc[screened["pocket_pivot"]].copy() if mode == "signals" else screened.loc[screened["watchlist"]].copy()
        candidates["timeframe"] = timeframe
        keep = [
            "date",
            "timeframe",
            "symbol",
            *([] if market != "macro" else ["asset_name", "macro_group"]),
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
        candidates = candidates[keep].sort_values(["date", "tier", "rps_max"], ascending=[False, True, False])
        candidates.to_csv(path, index=False)
    return outputs


def filter_us_tradable_universe(
    data: pd.DataFrame,
    min_price: float = 3.0,
    min_avg_dollar_volume: float = 5_000_000,
    min_bars: int = 250,
) -> pd.DataFrame:
    if data.empty:
        return data
    data = data.sort_values(["symbol", "date"]).copy()
    latest = data.groupby("symbol", as_index=False).tail(1).copy()
    latest_symbols = set(latest.loc[latest["close"] >= min_price, "symbol"])

    def liquidity_ok(group: pd.DataFrame) -> bool:
        if len(group) < min_bars:
            return False
        recent = group.tail(30)
        dollar_volume = (recent["close"] * recent["volume"]).mean()
        return bool(dollar_volume >= min_avg_dollar_volume)

    liquid_symbols = {
        symbol
        for symbol, group in data.groupby("symbol")
        if symbol in latest_symbols and liquidity_ok(group)
    }
    return data.loc[data["symbol"].isin(liquid_symbols)].copy()


def latest_report_dir(base_dir: Path) -> Path:
    return base_dir / datetime.now(UTC).strftime("%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default="crypto,us", help="Comma-separated: crypto,us,macro")
    parser.add_argument("--data-dir", type=Path, default=Path("data/rps_pp"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/rps_pp"))
    parser.add_argument("--env-file", type=Path, default=Path("docs/strategies/.env"))
    parser.add_argument(
        "--operation",
        choices=("update-and-scan", "backfill", "scan-only", "repair-missing"),
        default="update-and-scan",
        help="update-and-scan downloads data then ranks; backfill only downloads data; scan-only only ranks local data; repair-missing fills missing/short histories.",
    )
    parser.add_argument("--crypto-limit", type=int, default=200)
    parser.add_argument("--crypto-min-30d-volume", type=float, default=5_000_000)
    parser.add_argument("--crypto-timeframes", default="1d,4h")
    parser.add_argument("--crypto-lookback-days", type=int, default=420)
    parser.add_argument("--macro-lookback-days", type=int, default=420)
    parser.add_argument("--us-symbols-file", type=Path)
    parser.add_argument(
        "--us-universe",
        choices=("config", "sptm", "iwv", "polygon-common", "nasdaq"),
        default="config",
        help="US universe source. iwv uses IWV holdings as a Russell 3000 proxy.",
    )
    parser.add_argument("--us-fetch-universe", action="store_true")
    parser.add_argument("--us-max-symbols", type=int)
    parser.add_argument("--us-lookback-days", type=int, default=420)
    parser.add_argument("--force-backfill", action="store_true", help="Force full lookback for grouped US data.")
    parser.add_argument("--us-provider", choices=("polygon", "polygon-grouped", "yahoo"), default="polygon")
    parser.add_argument(
        "--us-request-sleep",
        type=float,
        default=12.0,
        help="Seconds to sleep between US data requests. Increase if Polygon rate-limits your plan.",
    )
    args = parser.parse_args()

    selected_markets = {item.strip() for item in args.markets.split(",") if item.strip()}
    report_dir = latest_report_dir(args.output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"generated_at": datetime.now(UTC).isoformat(), "outputs": {}}
    should_update = args.operation in {"update-and-scan", "backfill"}
    should_scan = args.operation in {"update-and-scan", "scan-only"}

    if "macro" in selected_markets:
        key = polygon_api_key(args.env_file)
        if should_update:
            update_macro_data(
                data_dir=args.data_dir,
                lookback_days=args.macro_lookback_days,
                polygon_key=key,
                request_sleep=args.us_request_sleep,
            )
        if should_scan:
            outputs = run_scans(args.data_dir / "macro_1d", report_dir, "macro", "1d", "macro_1d")
            manifest["outputs"]["macro_1d"] = {key: str(value) for key, value in outputs.items()}

    if "crypto" in selected_markets:
        for timeframe in [item.strip() for item in args.crypto_timeframes.split(",") if item.strip()]:
            if should_update:
                update_crypto_data(
                    data_dir=args.data_dir,
                    timeframe=timeframe,
                    limit=args.crypto_limit,
                    min_quote_volume_30d=args.crypto_min_30d_volume,
                    lookback_days=args.crypto_lookback_days,
                )
            input_dir = args.data_dir / f"crypto_{timeframe}"
            if should_scan:
                outputs = run_scans(input_dir, report_dir, "crypto", timeframe, f"crypto_{timeframe}")
                manifest["outputs"][f"crypto_{timeframe}"] = {key: str(value) for key, value in outputs.items()}

    if "us" in selected_markets:
        fetch_universe = args.us_fetch_universe or args.us_universe == "nasdaq"
        key = polygon_api_key(args.env_file)
        symbols = []
        if should_update or args.operation == "repair-missing":
            symbols = load_us_symbols(
                args.us_symbols_file,
                fetch_universe,
                args.us_max_symbols,
                args.us_universe,
                args.data_dir,
                key,
                args.us_request_sleep,
            )
        if args.operation == "repair-missing":
            repair_symbols = us_symbols_needing_history(args.data_dir, symbols, min_bars=250)
            provider = "polygon" if args.us_provider == "polygon-grouped" else args.us_provider
            update_us_data(
                args.data_dir,
                repair_symbols,
                args.us_lookback_days,
                provider,
                key,
                args.us_request_sleep,
            )
            manifest["outputs"]["us_repair_missing"] = {"symbols": len(repair_symbols)}
        elif should_update:
            if args.us_provider == "polygon-grouped":
                update_us_grouped_data(
                    args.data_dir,
                    symbols,
                    args.us_lookback_days,
                    key,
                    args.us_request_sleep,
                    force_backfill=args.force_backfill,
                )
            else:
                update_us_data(
                    args.data_dir,
                    symbols,
                    args.us_lookback_days,
                    args.us_provider,
                    key,
                    args.us_request_sleep,
                )
        if should_scan:
            outputs = run_scans(args.data_dir / "us_1d", report_dir, "us", "1d", "us_1d")
            manifest["outputs"]["us_1d"] = {key: str(value) for key, value in outputs.items()}

    manifest_path = report_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

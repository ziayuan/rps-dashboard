import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tools import rps_pocket_pivot_scanner as scanner
from tools.rps_daily_runner import (
    append_ohlcv,
    macro_asset_symbols,
    grouped_daily_to_symbol_frames,
    filter_us_tradable_universe,
    grouped_backfill_start,
    latest_polygon_grouped_available_date,
    load_env_values,
    market_date_range,
    parse_sptm_holdings,
    polygon_aggs_to_ohlcv,
    run_scans,
    update_us_data,
    us_symbols_needing_history,
)


class RpsDailyRunnerTest(unittest.TestCase):
    def test_load_env_values_reads_polygon_key_without_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("\n# comment\npolygon_key='abc123'\nOTHER=value\n")

            values = load_env_values(path)

            self.assertEqual(values["polygon_key"], "abc123")
            self.assertEqual(values["OTHER"], "value")

    def test_polygon_aggs_to_ohlcv_maps_adjusted_daily_bars(self):
        payload = {
            "status": "OK",
            "ticker": "AAPL",
            "results": [
                {"t": 1767225600000, "o": 100.0, "h": 110.0, "l": 95.0, "c": 108.0, "v": 12345},
                {"t": 1767312000000, "o": 108.0, "h": 112.0, "l": 101.0, "c": 105.0, "v": 23456},
            ],
        }

        df = polygon_aggs_to_ohlcv(payload)

        self.assertEqual(df.columns.tolist(), ["date", "open", "high", "low", "close", "volume"])
        self.assertEqual(df["date"].tolist(), ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"])
        self.assertEqual(df["close"].tolist(), [108.0, 105.0])
        self.assertEqual(df["volume"].tolist(), [12345.0, 23456.0])

    def test_parse_sptm_holdings_returns_stock_tickers(self):
        holdings = pd.DataFrame(
            [
                {"Ticker": "AAPL", "Name": "Apple Inc.", "Asset Class": "Equity"},
                {"Ticker": "BRK.B", "Name": "Berkshire Hathaway", "Asset Class": "Equity"},
                {"Ticker": "-", "Name": "Cash", "Asset Class": "Cash"},
                {"Ticker": None, "Name": "Other", "Asset Class": "Other"},
            ]
        )

        symbols = parse_sptm_holdings(holdings)

        self.assertEqual(symbols, ["AAPL", "BRK.B"])

    def test_grouped_daily_to_symbol_frames_filters_to_universe(self):
        payload = {
            "status": "OK",
            "results": [
                {"T": "AAPL", "t": 1767225600000, "o": 100, "h": 110, "l": 95, "c": 108, "v": 12345},
                {"T": "MSFT", "t": 1767225600000, "o": 200, "h": 210, "l": 195, "c": 205, "v": 23456},
                {"T": "SPY", "t": 1767225600000, "o": 300, "h": 310, "l": 295, "c": 305, "v": 34567},
            ],
        }

        frames = grouped_daily_to_symbol_frames(payload, {"AAPL", "MSFT"})

        self.assertEqual(sorted(frames.keys()), ["AAPL", "MSFT"])
        self.assertEqual(frames["AAPL"]["close"].iloc[0], 108.0)
        self.assertNotIn("SPY", frames)

    def test_macro_asset_symbols_include_core_macro_markets(self):
        symbols = macro_asset_symbols()

        self.assertIn("QQQ", symbols)
        self.assertIn("GLD", symbols)
        self.assertIn("TLT", symbols)
        self.assertIn("BTCUSDT", symbols)

    def test_run_scans_outputs_all_latest_macro_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "macro_1d"
            output_dir = Path(tmp) / "reports"
            data_dir.mkdir()
            for symbol, base in [("QQQ", 100.0), ("GLD", 200.0), ("BTCUSDT", 300.0)]:
                rows = []
                for idx in range(130):
                    close = base + idx
                    rows.append(
                        {
                            "date": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(days=idx),
                            "open": close - 1,
                            "high": close + 1,
                            "low": close - 2,
                            "close": close,
                            "volume": 1000 + idx,
                        }
                    )
                pd.DataFrame(rows).to_csv(data_dir / f"{symbol}.csv", index=False)

            outputs = run_scans(data_dir, output_dir, "macro", "1d", "macro_1d")

            watchlist = pd.read_csv(outputs["watchlist"])
            signals = pd.read_csv(outputs["signals"])
            self.assertEqual(set(watchlist["symbol"]), {"BTCUSDT", "GLD", "QQQ"})
            self.assertEqual(watchlist["rps_max"].tolist(), sorted(watchlist["rps_max"].tolist(), reverse=True))
            self.assertTrue({"rps20", "rps60", "rps120", "macro_group"}.issubset(watchlist.columns))
            self.assertEqual(set(signals["symbol"]), {"BTCUSDT", "GLD", "QQQ"})

    def test_run_scans_macro_uses_latest_common_asset_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "macro_1d"
            output_dir = Path(tmp) / "reports"
            data_dir.mkdir()
            for symbol, days in [("QQQ", 130), ("GLD", 130), ("BTCUSDT", 132)]:
                rows = []
                for idx in range(days):
                    close = 100 + idx
                    rows.append(
                        {
                            "date": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(days=idx),
                            "open": close,
                            "high": close,
                            "low": close,
                            "close": close,
                            "volume": 1000,
                        }
                    )
                pd.DataFrame(rows).to_csv(data_dir / f"{symbol}.csv", index=False)

            outputs = run_scans(data_dir, output_dir, "macro", "1d", "macro_1d")

            watchlist = pd.read_csv(outputs["watchlist"])
            self.assertEqual(set(watchlist["symbol"]), {"BTCUSDT", "GLD", "QQQ"})
            self.assertEqual(set(watchlist["date"]), {"2026-05-10 00:00:00+00:00"})

    def test_run_scans_macro_computes_rps_on_common_trading_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "macro_1d"
            output_dir = Path(tmp) / "reports"
            data_dir.mkdir()
            trading_dates = pd.bdate_range("2026-01-01", periods=130, tz="UTC")
            calendar_dates = pd.date_range("2026-01-01", periods=190, tz="UTC")
            for symbol, dates in [("QQQ", trading_dates), ("GLD", trading_dates), ("BTCUSDT", calendar_dates)]:
                rows = []
                for idx, date in enumerate(dates):
                    close = 100 + idx
                    rows.append(
                        {
                            "date": date,
                            "open": close,
                            "high": close,
                            "low": close,
                            "close": close,
                            "volume": 1000,
                        }
                    )
                pd.DataFrame(rows).to_csv(data_dir / f"{symbol}.csv", index=False)

            outputs = run_scans(data_dir, output_dir, "macro", "1d", "macro_1d")

            watchlist = pd.read_csv(outputs["watchlist"])
            self.assertEqual(set(watchlist["symbol"]), {"BTCUSDT", "GLD", "QQQ"})
            self.assertFalse(watchlist[["rps20", "rps60", "rps120"]].isna().any().any())

    def test_grouped_backfill_start_uses_full_lookback_when_coverage_is_too_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            pd.DataFrame(
                [
                    {
                        "date": "2026-06-12T04:00:00Z",
                        "open": 1,
                        "high": 2,
                        "low": 1,
                        "close": 1.5,
                        "volume": 100,
                    }
                ]
            ).to_csv(data_dir / "AAA.csv", index=False)

            start = grouped_backfill_start(
                data_dir=data_dir,
                symbols=[f"SYM{index}" for index in range(600)],
                lookback_days=420,
                now=pd.Timestamp("2026-06-14T00:00:00Z").to_pydatetime(),
            )

            self.assertEqual(start.isoformat(), "2025-04-20T00:00:00+00:00")

    def test_grouped_backfill_start_uses_incremental_when_existing_coverage_is_broad(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            for index in range(120):
                pd.DataFrame(
                    [
                        {
                            "date": "2026-06-12T00:00:00Z",
                            "open": 1,
                            "high": 2,
                            "low": 1,
                            "close": 1.5,
                            "volume": 100,
                        }
                    ]
                ).to_csv(data_dir / f"SYM{index}.csv", index=False)

            start = grouped_backfill_start(
                data_dir=data_dir,
                symbols=[f"SYM{index}" for index in range(600)],
                lookback_days=420,
                now=pd.Timestamp("2026-06-14T00:00:00Z").to_pydatetime(),
            )

            self.assertEqual(start.isoformat(), "2026-06-05T00:00:00+00:00")

    def test_grouped_backfill_start_can_force_full_lookback(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            for index in range(120):
                pd.DataFrame(
                    [
                        {
                            "date": "2026-06-12T00:00:00Z",
                            "open": 1,
                            "high": 2,
                            "low": 1,
                            "close": 1.5,
                            "volume": 100,
                        }
                    ]
                ).to_csv(data_dir / f"SYM{index}.csv", index=False)

            start = grouped_backfill_start(
                data_dir=data_dir,
                symbols=[f"SYM{index}" for index in range(600)],
                lookback_days=420,
                now=pd.Timestamp("2026-06-14T00:00:00Z").to_pydatetime(),
                force_backfill=True,
            )

            self.assertEqual(start.isoformat(), "2025-04-20T00:00:00+00:00")

    def test_market_date_range_skips_weekends(self):
        days = market_date_range(
            pd.Timestamp("2026-06-12T00:00:00Z").to_pydatetime(),
            pd.Timestamp("2026-06-15T00:00:00Z").to_pydatetime(),
        )

        self.assertEqual([day.strftime("%Y-%m-%d") for day in days], ["2026-06-12", "2026-06-15"])

    def test_latest_polygon_grouped_available_date_waits_for_eod_delay(self):
        latest = latest_polygon_grouped_available_date(
            pd.Timestamp("2026-06-16T03:21:00Z").to_pydatetime(),
            delay_hours=8,
        )

        self.assertEqual(latest.strftime("%Y-%m-%d"), "2026-06-12")

    def test_latest_polygon_grouped_available_date_includes_previous_session_after_delay(self):
        latest = latest_polygon_grouped_available_date(
            pd.Timestamp("2026-06-16T05:00:00Z").to_pydatetime(),
            delay_hours=8,
        )

        self.assertEqual(latest.strftime("%Y-%m-%d"), "2026-06-15")

    def test_filter_us_tradable_universe_keeps_liquid_common_like_rows(self):
        rows = []
        for symbol, close, volume in [("KEEP", 10.0, 1_000_000), ("CHEAP", 2.0, 10_000_000), ("ILLIQ", 20.0, 10_000)]:
            for day in range(260):
                rows.append(
                    {
                        "symbol": symbol,
                        "date": f"2026-01-{(day % 28) + 1:02d}T{day // 28:02d}:00:00Z",
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "volume": volume,
                    }
                )
        data = pd.DataFrame(rows)

        filtered = filter_us_tradable_universe(data, min_price=3, min_avg_dollar_volume=5_000_000, min_bars=250)

        self.assertEqual(filtered["symbol"].unique().tolist(), ["KEEP"])

    def test_us_symbols_needing_history_returns_missing_and_short_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            us_dir = data_dir / "us_1d"
            us_dir.mkdir()
            pd.DataFrame(
                [
                    {"date": f"2026-01-{(idx % 28) + 1:02d}", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100}
                    for idx in range(260)
                ]
            ).to_csv(us_dir / "AAA.csv", index=False)
            pd.DataFrame(
                [
                    {"date": f"2026-01-{(idx % 28) + 1:02d}", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100}
                    for idx in range(40)
                ]
            ).to_csv(us_dir / "BBB.csv", index=False)

            symbols = us_symbols_needing_history(data_dir, ["AAA", "BBB", "CCC"], min_bars=250)

            self.assertEqual(symbols, ["BBB", "CCC"])

    def test_append_ohlcv_merges_by_date_and_keeps_latest_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AAA.csv"
            existing = pd.DataFrame(
                [
                    {"date": "2026-01-01", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 100},
                    {"date": "2026-01-02", "open": 2, "high": 3, "low": 2, "close": 2.5, "volume": 200},
                ]
            )
            incoming = pd.DataFrame(
                [
                    {"date": "2026-01-02", "open": 2, "high": 4, "low": 2, "close": 3.5, "volume": 300},
                    {"date": "2026-01-03", "open": 3, "high": 4, "low": 3, "close": 3.5, "volume": 400},
                ]
            )
            existing.to_csv(path, index=False)

            append_ohlcv(path, incoming)

            merged = pd.read_csv(path)
            self.assertEqual(
                merged["date"].tolist(),
                ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"],
            )
            revised = merged.loc[merged["date"] == "2026-01-02T00:00:00Z"]
            self.assertEqual(revised["close"].iloc[0], 3.5)
            self.assertEqual(revised["volume"].iloc[0], 300)

    def test_append_ohlcv_deduplicates_same_market_date_with_different_utc_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AAA.csv"
            existing = pd.DataFrame(
                [
                    {"date": "2026-06-12T04:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 100},
                ]
            )
            incoming = pd.DataFrame(
                [
                    {"date": "2026-06-12T20:00:00Z", "open": 2, "high": 3, "low": 2, "close": 2.5, "volume": 200},
                ]
            )
            existing.to_csv(path, index=False)

            append_ohlcv(path, incoming)

            merged = pd.read_csv(path)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged["date"].tolist(), ["2026-06-12T00:00:00Z"])
            self.assertEqual(merged["close"].iloc[0], 2.5)

    def test_run_scans_writes_watchlist_and_signals_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            output_dir = root / "reports"
            data_dir.mkdir()

            for idx, symbol in enumerate(["AAA", "BBB", "CCC"]):
                rows = []
                for day in range(270):
                    close = 10 + idx * 2 + day * (0.04 - idx * 0.01)
                    rows.append(
                        {
                            "date": f"2026-01-{(day % 28) + 1:02d}T{day // 28:02d}:00:00Z",
                            "open": close * 0.99,
                            "high": close * 1.01,
                            "low": close * 0.98,
                            "close": close,
                            "volume": 1_000_000 + day,
                        }
                    )
                pd.DataFrame(rows).to_csv(data_dir / f"{symbol}.csv", index=False)

            outputs = run_scans(
                input_dir=data_dir,
                output_dir=output_dir,
                market="us",
                timeframe="1d",
                prefix="us_1d",
            )

            self.assertTrue(outputs["watchlist"].exists())
            self.assertTrue(outputs["signals"].exists())
            self.assertIn("watchlist", pd.read_csv(outputs["watchlist"]).columns)
            self.assertIn("pocket_pivot", pd.read_csv(outputs["signals"]).columns)

    def test_run_scans_outputs_us_rps30_without_changing_core_watchlist_basis(self):
        self.assertEqual(scanner.US_PERIODS, (30, 50, 120, 250))
        data = pd.DataFrame(
            {
                "rps_max": [96.0, 96.0],
                "rps30": [95.0, 80.0],
                "rps50": [80.0, 95.0],
                "close": [20.0, 20.0],
                "ma10": [19.0, 19.0],
                "ma20": [18.0, 18.0],
                "ma50": [17.0, 17.0],
                "ma150": [16.0, 16.0],
                "ma200": [15.0, 15.0],
                "ma200_20_ago": [15.0, 15.0],
                "highest_252": [22.0, 22.0],
                "highest_20_prev": [19.0, 19.0],
                "low": [18.0, 18.0],
                "up_day": [False, False],
                "volume_signature": [False, False],
            }
        )

        screened = scanner.add_us_screen_flags(data)

        self.assertFalse(bool(screened.loc[0, "core_watchlist"]))
        self.assertTrue(bool(screened.loc[1, "core_watchlist"]))

    def test_run_scans_outputs_only_latest_date_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            output_dir = root / "reports"
            data_dir.mkdir()
            for symbol, drift in [("AAA", 0.05), ("BBB", 0.01), ("CCC", -0.01)]:
                rows = []
                for day in range(280):
                    close = 10 + day * drift
                    rows.append(
                        {
                            "date": f"2026-01-{(day % 28) + 1:02d}T{day // 28:02d}:00:00Z",
                            "open": close * 0.99,
                            "high": close * 1.01,
                            "low": close * 0.98,
                            "close": close,
                            "volume": 1_000_000 + day,
                        }
                    )
                pd.DataFrame(rows).to_csv(data_dir / f"{symbol}.csv", index=False)

            outputs = run_scans(data_dir, output_dir, "us", "1d", "us_1d")
            watchlist = pd.read_csv(outputs["watchlist"])

            self.assertLessEqual(watchlist["date"].nunique(), 1)

    def test_update_us_data_retries_same_symbol_after_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            rate_limit = urllib.error.HTTPError(
                url="https://api.polygon.io/test",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=None,
            )
            success = pd.DataFrame(
                [
                    {
                        "date": "2026-01-01T00:00:00Z",
                        "open": 1.0,
                        "high": 2.0,
                        "low": 1.0,
                        "close": 1.5,
                        "volume": 1000.0,
                    }
                ]
            )

            with patch("tools.rps_daily_runner.fetch_polygon_daily", side_effect=[rate_limit, success]), patch(
                "tools.rps_daily_runner.time.sleep"
            ):
                kept = update_us_data(
                    data_dir=data_dir,
                    symbols=["AAA"],
                    lookback_days=30,
                    provider="polygon",
                    polygon_key="test-key",
                    request_sleep=0,
                    max_retries=2,
                )

            self.assertEqual(kept, ["AAA"])
            self.assertTrue((data_dir / "us_1d" / "AAA.csv").exists())


if __name__ == "__main__":
    unittest.main()

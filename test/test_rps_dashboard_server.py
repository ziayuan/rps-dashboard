import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.rps_dashboard_server import (
    available_report_dates,
    data_health_payload,
    latest_report_dir,
    read_csv_records,
    resolve_runner_python,
    refresh_args_for_action,
    refresh_command,
    table_payload,
)


class RpsDashboardServerTest(unittest.TestCase):
    def test_table_payload_reads_latest_report_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dir = root / "2026-06-12"
            latest_dir = root / "2026-06-13"
            old_dir.mkdir()
            latest_dir.mkdir()
            (old_dir / "us_1d_watchlist.csv").write_text("symbol,rps_max\nOLD,1\n")
            (latest_dir / "us_1d_watchlist.csv").write_text("symbol,rps_max\nAAA,99\nBBB,95\n")
            (latest_dir / "us_1d_signals.csv").write_text("symbol,rps_max,pocket_pivot\nAAA,99,True\n")
            (latest_dir / "macro_1d_watchlist.csv").write_text("symbol,rps_max\nQQQ,100\nGLD,80\n")

            self.assertEqual(latest_report_dir(root), latest_dir)
            self.assertEqual(available_report_dates(root), ["2026-06-12", "2026-06-13"])
            payload = table_payload(root)

            self.assertEqual(payload["reportDate"], "2026-06-13")
            self.assertEqual(payload["summary"]["us_1d_watchlist"], 2)
            self.assertEqual(payload["summary"]["us_1d_signals"], 1)
            self.assertEqual(payload["summary"]["macro_1d_watchlist"], 2)
            self.assertEqual(payload["tables"]["us_1d_signals"]["rows"][0]["symbol"], "AAA")
            self.assertEqual(payload["tables"]["macro_1d_watchlist"]["rows"][0]["symbol"], "QQQ")

            old_payload = table_payload(root, report_date="2026-06-12")
            self.assertEqual(old_payload["reportDate"], "2026-06-12")
            self.assertEqual(old_payload["tables"]["us_1d_watchlist"]["rows"][0]["symbol"], "OLD")

    def refresh_args(self) -> Namespace:
        return Namespace(
            markets="us",
            data_dir=Path("data/rps_pp"),
            report_root=Path("reports/rps_pp"),
            env_file=Path("docs/strategies/.env"),
            us_universe="iwv",
            us_provider="polygon-grouped",
            us_request_sleep=12.0,
            us_lookback_days=420,
            force_backfill=False,
            operation="update-and-scan",
            crypto_limit=200,
            crypto_timeframes="4h",
            crypto_lookback_days=420,
            macro_lookback_days=420,
            runner_python=None,
        )

    def test_data_health_payload_counts_missing_and_short_us_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "universe"
            us_dir = root / "us_1d"
            universe.mkdir()
            us_dir.mkdir()
            (universe / "polygon_common_stocks.csv").write_text("Ticker\nAAA\nBBB\nCCC\nDDD\n")
            full_rows = "date,open,high,low,close,volume\n" + "\n".join(
                f"2026-01-{(idx % 28) + 1:02d},1,1,1,1,100" for idx in range(260)
            )
            short_rows = "date,open,high,low,close,volume\n" + "\n".join(
                f"2026-01-{(idx % 28) + 1:02d},1,1,1,1,100" for idx in range(40)
            )
            (us_dir / "AAA.csv").write_text(full_rows)
            (us_dir / "BBB.csv").write_text(short_rows)

            payload = data_health_payload(root)

            self.assertEqual(payload["us"]["universeCount"], 4)
            self.assertEqual(payload["us"]["localCsvCount"], 2)
            self.assertEqual(payload["us"]["missingCsvCount"], 2)
            self.assertEqual(payload["us"]["shortHistoryCount"], 1)
            self.assertEqual(payload["us"]["rankableHistoryCount"], 1)

    def test_data_health_payload_counts_macro_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macro_dir = root / "macro_1d"
            macro_dir.mkdir()
            full_rows = "date,open,high,low,close,volume\n" + "\n".join(
                f"2026-01-{(idx % 28) + 1:02d},1,1,1,1,100" for idx in range(130)
            )
            short_rows = "date,open,high,low,close,volume\n" + "\n".join(
                f"2026-01-{(idx % 28) + 1:02d},1,1,1,1,100" for idx in range(40)
            )
            (macro_dir / "QQQ.csv").write_text(full_rows)
            (macro_dir / "BTCUSDT.csv").write_text(short_rows)

            payload = data_health_payload(root)

            self.assertEqual(payload["macro"]["localCsvCount"], 2)
            self.assertEqual(payload["macro"]["shortHistoryCount"], 1)
            self.assertEqual(payload["macro"]["rankableHistoryCount"], 1)

    def test_data_health_payload_uses_macro_common_latest_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macro_dir = root / "macro_1d"
            macro_dir.mkdir()
            (macro_dir / "QQQ.csv").write_text(
                "date,open,high,low,close,volume\n"
                + "\n".join(f"2026-06-{day:02d},1,1,1,1,100" for day in range(1, 13))
            )
            (macro_dir / "BTCUSDT.csv").write_text(
                "date,open,high,low,close,volume\n"
                + "\n".join(f"2026-06-{day:02d},1,1,1,1,100" for day in range(1, 15))
            )

            payload = data_health_payload(root)

            self.assertEqual(payload["macro"]["latestDate"], "2026-06-12")

    def test_read_csv_records_orders_tier_a_before_lower_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.csv"
            path.write_text(
                "date,symbol,tier,rps_max\n"
                "2026-06-12,BTCUSDT,C,20\n"
                "2026-06-12,QQQ,A,100\n"
                "2026-06-12,SPY,B,70\n"
            )

            rows = read_csv_records(path)

            self.assertEqual([row["symbol"] for row in rows], ["QQQ", "SPY", "BTCUSDT"])

    def test_refresh_args_for_action_scopes_to_requested_action(self):
        args = self.refresh_args()

        us_backfill = refresh_args_for_action(args, "us-backfill")
        self.assertEqual(us_backfill.markets, "us")
        self.assertEqual(us_backfill.operation, "backfill")

        crypto_rank = refresh_args_for_action(args, "crypto-rank")
        self.assertEqual(crypto_rank.markets, "crypto")
        self.assertEqual(crypto_rank.operation, "scan-only")

        macro_backfill = refresh_args_for_action(args, "macro-backfill")
        self.assertEqual(macro_backfill.markets, "macro")
        self.assertEqual(macro_backfill.operation, "backfill")

        macro_rank = refresh_args_for_action(args, "macro-rank")
        self.assertEqual(macro_rank.markets, "macro")
        self.assertEqual(macro_rank.operation, "scan-only")

        us_repair = refresh_args_for_action(args, "us-repair-missing")
        self.assertEqual(us_repair.markets, "us")
        self.assertEqual(us_repair.operation, "repair-missing")

        with self.assertRaises(ValueError):
            refresh_args_for_action(args, "bonds")

    def test_refresh_command_includes_crypto_4h_when_action_is_crypto_backfill(self):
        args = refresh_args_for_action(self.refresh_args(), "crypto-backfill")

        command = refresh_command(args)

        self.assertIn("--markets", command)
        self.assertIn("crypto", command)
        self.assertIn("--operation", command)
        self.assertIn("backfill", command)
        self.assertNotIn("--us-provider", command)
        self.assertIn("--crypto-timeframes", command)
        self.assertIn("4h", command)

    def test_refresh_command_includes_crypto_4h_when_action_is_crypto_rank(self):
        args = refresh_args_for_action(self.refresh_args(), "crypto-rank")

        command = refresh_command(args)

        self.assertIn("scan-only", command)
        self.assertIn("--crypto-timeframes", command)
        self.assertIn("4h", command)
        self.assertNotIn("--crypto-limit", command)

    def test_refresh_command_includes_macro_lookback_when_action_is_macro_backfill(self):
        args = refresh_args_for_action(self.refresh_args(), "macro-backfill")

        command = refresh_command(args)

        self.assertIn("--markets", command)
        self.assertIn("macro", command)
        self.assertIn("--operation", command)
        self.assertIn("backfill", command)
        self.assertIn("--macro-lookback-days", command)
        self.assertNotIn("--crypto-timeframes", command)
        self.assertNotIn("--us-provider", command)

    def test_refresh_command_includes_us_provider_when_action_is_us_backfill(self):
        args = refresh_args_for_action(self.refresh_args(), "us-backfill")

        command = refresh_command(args)

        self.assertIn("--markets", command)
        self.assertIn("us", command)
        self.assertIn("--operation", command)
        self.assertIn("backfill", command)
        self.assertIn("--us-provider", command)
        self.assertNotIn("--crypto-timeframes", command)

    def test_refresh_command_repair_missing_uses_us_update_flags(self):
        args = refresh_args_for_action(self.refresh_args(), "us-repair-missing")

        command = refresh_command(args)

        self.assertIn("repair-missing", command)
        self.assertIn("--us-provider", command)
        self.assertNotIn("--crypto-timeframes", command)

    def test_refresh_command_scan_only_does_not_include_provider_specific_update_flags(self):
        args = refresh_args_for_action(self.refresh_args(), "us-rank")

        command = refresh_command(args)

        self.assertIn("scan-only", command)
        self.assertNotIn("--us-provider", command)
        self.assertNotIn("--crypto-timeframes", command)

    def test_refresh_command_uses_explicit_runner_python(self):
        args = self.refresh_args()
        args.runner_python = "/tmp/rps-python"

        command = refresh_command(args)

        self.assertEqual(command[0], "/tmp/rps-python")

    def test_resolve_runner_python_skips_candidates_without_pandas(self):
        args = self.refresh_args()
        args.runner_python = None
        candidates = ["/tmp/no-pandas-python", "/tmp/with-pandas-python"]

        chosen = resolve_runner_python(
            args,
            candidates=candidates,
            can_import=lambda executable, module: executable == "/tmp/with-pandas-python" and module == "pandas",
        )

        self.assertEqual(chosen, "/tmp/with-pandas-python")


if __name__ == "__main__":
    unittest.main()

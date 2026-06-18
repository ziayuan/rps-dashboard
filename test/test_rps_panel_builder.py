import csv
import tempfile
import unittest
from pathlib import Path

from tools.rps_panel_builder import build_panels


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class RpsPanelBuilderTest(unittest.TestCase):
    def test_build_panels_summarizes_ab_core_by_investment_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "reports" / "2026-06-16"
            data_dir = root / "data"
            write_csv(
                report_dir / "us_1d_watchlist.csv",
                [
                    {
                        "date": "2026-06-15",
                        "symbol": "CHIP",
                        "tier": "A",
                        "core_watchlist": "True",
                        "rps_max": "99",
                        "rps30": "98",
                        "rps50": "97",
                        "rps120": "96",
                        "rps250": "95",
                    },
                    {
                        "date": "2026-06-15",
                        "symbol": "SEMI",
                        "tier": "B",
                        "core_watchlist": "True",
                        "rps_max": "97",
                        "rps30": "95",
                        "rps50": "94",
                        "rps120": "93",
                        "rps250": "92",
                    },
                    {
                        "date": "2026-06-15",
                        "symbol": "CLOUD",
                        "tier": "C",
                        "core_watchlist": "True",
                        "rps_max": "96",
                        "rps30": "91",
                        "rps50": "90",
                        "rps120": "89",
                        "rps250": "88",
                    },
                ],
            )
            write_csv(
                data_dir / "metadata" / "us_company_profiles.csv",
                [
                    {
                        "symbol": "CHIP",
                        "name": "AI Chip Corp",
                        "sector": "Technology",
                        "industry": "Semiconductors",
                        "sic_description": "SEMICONDUCTORS & RELATED DEVICES",
                        "description": "data center artificial intelligence semiconductor connectivity",
                        "market_cap": "1000000000",
                        "source": "test",
                        "updated_at": "2026-06-16T00:00:00Z",
                    },
                    {
                        "symbol": "SEMI",
                        "name": "Semi Tools Inc",
                        "sector": "Technology",
                        "industry": "Semiconductor Equipment",
                        "sic_description": "SPECIAL INDUSTRY MACHINERY",
                        "description": "wafer fabrication equipment for semiconductor manufacturers",
                        "market_cap": "500000000",
                        "source": "test",
                        "updated_at": "2026-06-16T00:00:00Z",
                    },
                ],
            )

            payload = build_panels(
                data_dir=data_dir,
                report_root=root / "reports",
                report_date="2026-06-16",
                fetch_profiles=False,
            )

            self.assertEqual(payload["reportDate"], "2026-06-16")
            self.assertEqual(payload["source"]["abCoreCount"], 2)
            themes = {row["theme"]: row for row in payload["themePanel"]["rows"]}
            self.assertEqual(themes["AI/数据中心芯片"]["count"], 1)
            self.assertEqual(themes["AI/数据中心芯片"]["avgRps50"], 97.0)
            self.assertEqual(themes["半导体设备/EDA/封测"]["medianRps120"], 93.0)
            self.assertEqual(payload["themePanel"]["detailRows"][0]["symbol"], "CHIP")

    def test_build_panels_marks_former_leaders_from_history_and_manual_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_root = root / "reports"
            data_dir = root / "data"
            write_csv(
                report_root / "2026-06-15" / "us_1d_watchlist.csv",
                [
                    {
                        "date": "2026-06-14",
                        "symbol": "OLD",
                        "tier": "A",
                        "core_watchlist": "True",
                        "rps_max": "99",
                        "rps30": "98",
                        "rps50": "97",
                        "rps120": "96",
                        "rps250": "95",
                    }
                ],
            )
            write_csv(
                report_root / "2026-06-16" / "us_1d_watchlist.csv",
                [
                    {
                        "date": "2026-06-15",
                        "symbol": "NEW",
                        "tier": "A",
                        "core_watchlist": "True",
                        "rps_max": "99",
                        "rps30": "98",
                        "rps50": "97",
                        "rps120": "96",
                        "rps250": "95",
                    }
                ],
            )
            write_csv(
                data_dir / "metadata" / "us_manual_leaders.csv",
                [{"symbol": "MANUAL", "note": "手动观察"}],
            )

            payload = build_panels(
                data_dir=data_dir,
                report_root=report_root,
                report_date="2026-06-16",
                fetch_profiles=False,
            )

            current_symbols = {row["symbol"] for row in payload["leadership"]["current"]}
            former_symbols = {row["symbol"] for row in payload["leadership"]["former"]}
            self.assertIn("NEW", current_symbols)
            self.assertIn("OLD", former_symbols)
            self.assertIn("MANUAL", former_symbols)

    def test_build_panels_uses_macro_watchlist_for_chinese_regime_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_root = root / "reports"
            data_dir = root / "data"
            write_csv(
                report_root / "2026-06-16" / "us_1d_watchlist.csv",
                [
                    {
                        "date": "2026-06-15",
                        "symbol": "AAA",
                        "tier": "B",
                        "core_watchlist": "True",
                        "rps_max": "96",
                        "rps30": "95",
                        "rps50": "94",
                        "rps120": "93",
                        "rps250": "92",
                    }
                ],
            )
            write_csv(
                report_root / "2026-06-16" / "macro_1d_watchlist.csv",
                [
                    {"symbol": "QQQ", "asset_name": "Nasdaq 100", "macro_group": "科技成长", "rps_max": "95", "rps20": "96", "rps60": "94", "rps120": "93"},
                    {"symbol": "UUP", "asset_name": "US Dollar", "macro_group": "美元", "rps_max": "20", "rps20": "22", "rps60": "25", "rps120": "30"},
                    {"symbol": "HYG", "asset_name": "High Yield", "macro_group": "信用", "rps_max": "70", "rps20": "72", "rps60": "74", "rps120": "76"},
                ],
            )

            payload = build_panels(
                data_dir=data_dir,
                report_root=report_root,
                report_date="2026-06-16",
                fetch_profiles=False,
            )

            self.assertIn(payload["macroRegime"]["regime"], {"风险偏好", "中性", "风险警戒", "风险回避"})
            self.assertIn("科技成长", payload["macroRegime"]["summary"])
            self.assertGreaterEqual(len(payload["macroRegime"]["indicators"]), 3)


if __name__ == "__main__":
    unittest.main()

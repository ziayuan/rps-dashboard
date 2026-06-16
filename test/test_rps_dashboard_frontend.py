from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read_dashboard_file(name: str) -> str:
    return (ROOT / "rps-dashboard" / name).read_text()


class RpsDashboardFrontendTest(unittest.TestCase):
    def test_dashboard_exposes_separate_backfill_and_rank_buttons(self):
        html = read_dashboard_file("index.html")
        script = read_dashboard_file("app.js")

        self.assertIn('id="backfillUsBtn"', html)
        self.assertIn('id="backfillCryptoBtn"', html)
        self.assertIn('id="backfillMacroBtn"', html)
        self.assertIn('id="repairMissingUsBtn"', html)
        self.assertIn('id="rankUsBtn"', html)
        self.assertIn('id="rankCryptoBtn"', html)
        self.assertIn('id="rankMacroBtn"', html)
        self.assertIn('runAction("us-backfill")', script)
        self.assertIn('runAction("crypto-backfill")', script)
        self.assertIn('runAction("macro-backfill")', script)
        self.assertIn('runAction("us-repair-missing")', script)
        self.assertIn('runAction("us-rank")', script)
        self.assertIn('runAction("crypto-rank")', script)
        self.assertIn('runAction("macro-rank")', script)
        self.assertNotIn('id="refreshBtn"', html)

    def test_dashboard_exposes_data_health_panel(self):
        html = read_dashboard_file("index.html")
        script = read_dashboard_file("app.js")

        self.assertIn('id="healthPanel"', html)
        self.assertIn('id="usHealthText"', html)
        self.assertIn('id="cryptoHealthText"', html)
        self.assertIn('id="macroHealthText"', html)
        self.assertIn("loadHealth", script)
        self.assertIn("/api/health", script)

    def test_dashboard_exposes_tier_filter_control(self):
        html = read_dashboard_file("index.html")
        script = read_dashboard_file("app.js")

        self.assertIn('id="tierFilter"', html)
        self.assertIn('id="tierMenu"', html)
        self.assertIn('type="checkbox"', html)
        self.assertIn('data-tier="A"', html)
        self.assertIn('data-tier="B"', html)
        self.assertIn('data-tier="C"', html)
        self.assertIn("getSelectedTiers", script)
        self.assertIn("updateTierButton", script)

    def test_dashboard_displays_full_us_rps_period_columns(self):
        script = read_dashboard_file("app.js")

        self.assertIn('"rps30"', script)
        self.assertIn('"rps50"', script)
        self.assertIn('"rps120"', script)
        self.assertIn('"rps250"', script)
        self.assertIn("US_RPS_COLUMNS", script)

    def test_dashboard_displays_macro_rps_table(self):
        script = read_dashboard_file("app.js")

        self.assertIn("Macro RPS", script)
        self.assertIn("MACRO_RPS_COLUMNS", script)
        self.assertIn('"rps20"', script)
        self.assertIn('"rps60"', script)
        self.assertIn('"rps120"', script)
        self.assertIn('"macro_group"', script)

    def test_dashboard_exposes_us_rps_threshold_filters(self):
        html = read_dashboard_file("index.html")
        script = read_dashboard_file("app.js")

        self.assertIn('id="rps30Min"', html)
        self.assertIn('id="rps50Min"', html)
        self.assertIn('id="rps120Min"', html)
        self.assertIn('id="rps250Min"', html)
        self.assertIn("rpsThresholdPasses", script)
        self.assertIn('"rps30"', script)
        self.assertIn('"rps50"', script)
        self.assertIn('"rps120"', script)
        self.assertIn('"rps250"', script)


if __name__ == "__main__":
    unittest.main()

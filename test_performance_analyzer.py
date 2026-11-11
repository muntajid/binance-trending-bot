"""Offline tests for the performance feedback-loop analyzer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot import analyze_performance


CSV = """published_utc,url,type,coin,hook_family,views,likes,replies,shares,followers_gain,notes
2026-11-01T08:00:00Z,http://x/1,explained_setup,ERA,contrarian_conflict,30000,60,40,,,
2026-11-02T08:00:00Z,http://x/2,explained_setup,BTC,binary_scenario,12000,30,10,,,
2026-11-03T08:00:00Z,http://x/3,rapid_alert,TIA,data_finding,9000,20,5,,,
2026-11-04T08:00:00Z,http://x/4,meme_community,,outcome_update,25000,90,60,,,
2026-11-05T08:00:00Z,http://x/5,market_overview,BTC,data_finding,15000,25,10,,,
bad-date,http://x/6,rapid_alert,AMP,structure_break,5000,10,2,,,
2026-11-07T08:00:00Z,http://x/7,rapid_alert,AMP,,not-a-number,10,2,,,
"""


class AnalyzerTests(unittest.TestCase):
    def _rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "posts.csv"
            path.write_text(CSV, encoding="utf-8")
            return analyze_performance.load_rows(path)

    def test_rows_skip_unparseable_views(self):
        rows = self._rows()
        self.assertEqual(len(rows), 6)  # the "not-a-number" views row is dropped

    def test_engagement_formula_matches_research_report(self):
        rows = self._rows()
        first = next(row for row in rows if row["url"] == "http://x/1")
        self.assertAlmostEqual(first["engagement_rate"], (60 + 40) / 30000 * 100.0)
        summary = analyze_performance.summarize(rows)
        expected_pooled = (60 + 40 + 30 + 10 + 20 + 5 + 90 + 60 + 25 + 10 + 10 + 2) / (
            30000 + 12000 + 9000 + 25000 + 15000 + 5000
        ) * 100.0
        self.assertAlmostEqual(summary["pooled_engagement"], expected_pooled)

    def test_days_filter(self):
        rows = self._rows()
        self.assertEqual(len(rows), 6)
        import datetime

        recent = analyze_performance.load_rows(
            _write(CSV),
            days=3,
            now=datetime.datetime(2026, 11, 8, tzinfo=datetime.timezone.utc),
        )
        # Only the Nov 5 row survives: Nov 7's row is already dropped for an
        # unparseable view count, and the undated row cannot be proven inside
        # the window.
        self.assertEqual([row["url"] for row in recent], ["http://x/5"])

    def test_report_states_sample_size_caution(self):
        report = analyze_performance.render_report(self._rows())
        self.assertIn("low sample", report)
        self.assertIn("Bottom 3", report)
        self.assertIn("Suggested next test", report)
        self.assertIn("not signal", report.lower())

    def test_empty_log_is_polite(self):
        self.assertIn("No performance rows", analyze_performance.render_report([]))


def _write(content: str) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


if __name__ == "__main__":
    unittest.main()

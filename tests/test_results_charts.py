#!/usr/bin/env python3
"""The result charts are drawn from the committed data: every SVG must be well-formed, themed for dark mode, and show the numbers of the data.
Run: python3 tests/test_results_charts.py"""
import json, os, sys, tempfile, unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import results_charts  # noqa: E402


class Charts(unittest.TestCase):
    def test_svgs_are_well_formed_themed_and_show_the_data(self):
        with tempfile.TemporaryDirectory() as d:
            results_charts.main(d)
            names = sorted(os.listdir(d))
            self.assertEqual(names, ["results-bench.svg", "results-gate.svg", "results-headline.svg", "results-pack.svg", "results-parallel.svg", "results-upgrades.svg"])
            for n in names:
                text = open(os.path.join(d, n), encoding="utf-8").read()
                root = ET.fromstring(text)
                self.assertTrue(root.tag.endswith("svg"), n)
                self.assertIn("prefers-color-scheme: dark", text, n)
                self.assertIn("<title", text, n)
                limit = {"t": 68, "s": 100, "l": 40, "v": 40, "h": 60, "b": 8}
                for el in root.iter("{http://www.w3.org/2000/svg}text"):
                    cls = (el.get("class") or "").split()
                    if cls and cls[0] in limit:
                        self.assertLessEqual(len(el.text or ""), limit[cls[0]], f"{n}: text too long for the image width: {el.text!r}")
            par = open(os.path.join(d, "results-parallel.svg"), encoding="utf-8").read()
            data = json.load(open(os.path.join(ROOT, "docs", "parallel-data.json")))
            self.assertIn(f"{data['by_workers_running'][-1]['ok_runs_per_hour_team']:.1f}/h", par)

    def test_committed_images_are_current(self):
        with tempfile.TemporaryDirectory() as d:
            results_charts.main(d)
            for n in os.listdir(d):
                self.assertEqual(open(os.path.join(d, n), encoding="utf-8").read(),
                                 open(os.path.join(ROOT, "docs", "img", n), encoding="utf-8").read(), f"{n} is stale: python3 tools/results_charts.py")


if __name__ == "__main__":
    unittest.main()

"""Tests for cli: each subcommand through main(), including usage errors and store errors."""
import unittest

import helpers


class CliTest(unittest.TestCase):
    def test_add_then_list(self):
        with helpers.TempStore() as t:
            code, out, _ = helpers.run_cli(["add", "api", "1h30m", "--start", "2024-03-04T09:00", "--tags", "Backend, api"], t.path)
            self.assertEqual((code, out.strip()), (0, "added #1: api 1h30m"))
            code, out, _ = helpers.run_cli(["list"], t.path)
            self.assertEqual(code, 0)
            self.assertIn("backend,api", out)

    def test_list_filters_by_tag_and_since(self):
        with helpers.TempStore() as t:
            t.fill()
            _, out, _ = helpers.run_cli(["list", "--tag", "frontend"], t.path)
            self.assertEqual(len(out.splitlines()), 2)
            _, out, _ = helpers.run_cli(["list", "--since", "2024-03-07"], t.path)
            self.assertEqual(len(out.splitlines()), 3)
            _, out, _ = helpers.run_cli(["list", "--since", "2030-01-01"], t.path)
            self.assertEqual(out.strip(), "No entries.")

    def test_report_and_export(self):
        with helpers.TempStore() as t:
            t.fill()
            code, out, _ = helpers.run_cli(["report", "month"], t.path)
            self.assertEqual((code, out.splitlines()[1]), (0, "2024-03  5h45m"))
            code, out, _ = helpers.run_cli(["export", "--format", "json"], t.path)
            self.assertEqual(code, 0)
            self.assertTrue(out.lstrip().startswith("["))

    def test_usage_errors_exit_2(self):
        with helpers.TempStore() as t:
            for argv in (["add", "x", "soon"], ["list", "--since", "yesterday"], ["report", "bogus"], ["nope"]):
                with self.subTest(argv=argv):
                    self.assertEqual(helpers.run_cli(argv, t.path)[0], 2)

    def test_corrupt_store_exits_1(self):
        with helpers.TempStore() as t:
            with open(t.path, "w") as f:
                f.write("[")
            code, _, err = helpers.run_cli(["list"], t.path)
            self.assertEqual(code, 1)
            self.assertIn("not valid JSON", err)


if __name__ == "__main__":
    unittest.main()

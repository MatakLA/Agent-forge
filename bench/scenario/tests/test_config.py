"""Tests for config: file locations from the environment and loading the optional config file."""
import json
import os
import unittest

import helpers
import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        self.tmp = helpers.TempStore().__enter__()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        self.tmp.__exit__(None, None, None)

    def test_environment_overrides_locations(self):
        os.environ["TASKLOG_STORE"] = "/x/s.json"
        os.environ["TASKLOG_CONFIG"] = "/x/c.json"
        self.assertEqual((config.store_path(), config.config_path()), ("/x/s.json", "/x/c.json"))

    def test_missing_file_gives_defaults(self):
        os.environ["TASKLOG_CONFIG"] = os.path.join(self.tmp.dir.name, "none.json")
        self.assertEqual(config.load_config(), config.DEFAULTS)

    def test_file_overrides_known_keys_only(self):
        path = os.path.join(self.tmp.dir.name, "c.json")
        with open(path, "w") as f:
            json.dump({"default_tags": ["misc"], "bogus": 1}, f)
        os.environ["TASKLOG_CONFIG"] = path
        self.assertEqual(config.load_config(), {"default_tags": ["misc"], "round_to": 1})


if __name__ == "__main__":
    unittest.main()

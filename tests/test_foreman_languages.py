#!/usr/bin/env python3
"""Tests for the four separate project languages (OWNER_LANG, TASK_LANG, PRODUCT_LANG, DOCS_LANG).
Public interface: python3 tests/test_foreman_languages.py or unittest discovery.
Invariants: each language is validated and settable on its own; PRODUCT_LANG may be empty (= the owner's language); the
orchestrator system prompt and the worker prompt name every language.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-test-lang-")
os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = T
os.environ["LIMITS_FILE"] = os.path.join(ROOT, "etc", "limits.example.conf")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

from foreman import ctx
from foreman.knowledge import language_rules, languages, load_system_prompt
from foreman.prompts import rules_block
from foreman.settings import EDITABLE


class TestLanguages(unittest.TestCase):
    def setUp(self):
        self.saved = {k: ctx.C.get(k) for k in ("OWNER_LANG", "TASK_LANG", "PRODUCT_LANG", "DOCS_LANG")}

    def tearDown(self):
        ctx.C.update(self.saved)

    def test_defaults(self):
        self.assertEqual(ctx.CONF["TASK_LANG"], "English")
        self.assertEqual(ctx.CONF["DOCS_LANG"], "English")
        self.assertEqual(ctx.CONF["PRODUCT_LANG"], "")

    def test_validation(self):
        import re
        for k in ("OWNER_LANG", "TASK_LANG", "DOCS_LANG", "PRODUCT_LANG"):
            rx = EDITABLE[k][0]
            self.assertTrue(re.fullmatch(rx, "Slovak"), k)
            self.assertTrue(re.fullmatch(rx, "Brazilian Portuguese"), k)
            self.assertFalse(re.fullmatch(rx, "English; rm -rf /"), k)
            self.assertFalse(re.fullmatch(rx, "x\nOWNER_LANG=y"), k)
        self.assertTrue(re.fullmatch(EDITABLE["PRODUCT_LANG"][0], ""))
        self.assertFalse(re.fullmatch(EDITABLE["TASK_LANG"][0], ""))

    def test_independent_and_product_falls_back_to_owner(self):
        ctx.C.update(OWNER_LANG="Slovak", TASK_LANG="English", PRODUCT_LANG="", DOCS_LANG="Czech")
        self.assertEqual(languages(), dict(owner="Slovak", task="English", product="Slovak", docs="Czech"))
        ctx.C.update(PRODUCT_LANG="German")
        self.assertEqual(languages()["product"], "German")

    def test_prompts_name_every_language(self):
        ctx.C.update(OWNER_LANG="Slovak", TASK_LANG="English", PRODUCT_LANG="German", DOCS_LANG="Czech")
        rules = language_rules()
        for lang in ("Slovak", "English", "German", "Czech"):
            self.assertIn(f"**{lang}**", rules)
        self.assertIn("## Languages", load_system_prompt())
        wb = rules_block(dict(branch="b", id="0001"))
        self.assertIn("in English", wb)
        self.assertIn("in Czech", wb)
        self.assertIn("in German", wb)


if __name__ == "__main__":
    unittest.main(verbosity=0)

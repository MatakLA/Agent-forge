"""Tests for path traversal prevention and path confinement in foreman/knowledge.py.
Covers doc_path, doc_get, doc_set, doc_reset, and role_playbook against directory traversal payloads,
absolute paths, and malformed inputs while ensuring legitimate document and role names resolve correctly.
Invariants: no path containing '..', '/', '\\', or absolute components resolves to a filesystem target."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

try:
    from foreman import ctx, knowledge  # noqa: E402
except Exception as err:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestKnowledgePaths(unittest.TestCase):

    def test_doc_path_traversal_rejected(self):
        """Invariant: doc_path returns None for traversal, absolute, or malformed document names."""
        assert knowledge.doc_path("../../../etc/passwd") is None
        assert knowledge.doc_path("role:../../etc/passwd") is None
        assert knowledge.doc_path("/etc/passwd") is None
        assert knowledge.doc_path("../../../etc/passwd", write=True) is None
        assert knowledge.doc_path("role:../../etc/passwd", write=True) is None
        assert knowledge.doc_path(123) is None
        assert knowledge.doc_path([]) is None
        assert knowledge.doc_path(None) is None
        assert knowledge.doc_path("", write=True) is None
        assert knowledge.doc_path(123, write=True) is None
        assert knowledge.doc_path([], write=True) is None
        assert knowledge.doc_path("role:123-developer") is None

        adversarial_names = [
            "../../../etc/passwd",
            "../../etc/passwd",
            "../x",
            "/etc/passwd",
            "/absolute/path/doc",
            "role:../../etc/passwd",
            "role:../../../etc/passwd",
            "role:/etc/passwd",
            "role:../roles/tester",
            "role:tester/../../etc/passwd",
            "..\\windows\\path",
            "role:..\\windows\\path",
            "vision/../../etc/passwd",
            "team-rules/../evil",
            None,
            "",
            123,
            [],
            "unknown-doc",
            "role:",
            "role:invalid_slug_with_underscores",
            "role:a",  # too short for ROLE_SLUG
            "role:123-developer",  # starts with digit
            "role:1-tester",
        ]
        for name in adversarial_names:
            assert knowledge.doc_path(name) is None
            assert knowledge.doc_path(name, write=True) is None
            self.assertIsNone(knowledge.doc_path(name), f"doc_path did not reject {name!r}")
            self.assertIsNone(knowledge.doc_path(name, write=True), f"doc_path(write=True) did not reject {name!r}")

    def test_doc_set_traversal_rejected(self):
        """Invariant: doc_set rejects path traversal and unknown names with 'unknown document'."""
        assert knowledge.doc_set("../../../etc/passwd", "payload") == "unknown document"
        assert knowledge.doc_set("role:../../etc/passwd", "payload") == "unknown document"
        assert knowledge.doc_set("/etc/passwd", "payload") == "unknown document"
        assert knowledge.doc_set(None, "payload") == "unknown document"
        assert knowledge.doc_set(123, "payload") == "unknown document"
        assert knowledge.doc_set("role:123-developer", "payload") == "unknown document"
        assert knowledge.doc_set("vision", 123) == "not a text"

        adversarial_names = [
            "../../../etc/passwd",
            "role:../../etc/passwd",
            "/etc/passwd",
            "role:/etc/passwd",
            "../backend-developer",
            None,
            "",
            123,
            [],
            "unknown-doc",
            "role:123-developer",
        ]
        for name in adversarial_names:
            err = knowledge.doc_set(name, "malicious payload")
            assert err == "unknown document"
            self.assertEqual(err, "unknown document", f"doc_set did not reject {name!r}")

    def test_doc_reset_traversal_rejected(self):
        """Invariant: doc_reset rejects non-roles and path traversal with 'only a role can be reset'."""
        assert knowledge.doc_reset("../../../etc/passwd") == "only a role can be reset"
        assert knowledge.doc_reset("role:../../etc/passwd") == "only a role can be reset"
        assert knowledge.doc_reset("/etc/passwd") == "only a role can be reset"
        assert knowledge.doc_reset(None) == "only a role can be reset"
        assert knowledge.doc_reset(123) == "only a role can be reset"
        assert knowledge.doc_reset("role:123-developer") == "only a role can be reset"
        assert knowledge.doc_reset("vision") == "only a role can be reset"

        adversarial_names = [
            "../../../etc/passwd",
            "role:../../etc/passwd",
            "/etc/passwd",
            "role:/etc/passwd",
            "role:../roles/tester",
            "role:123-developer",
            None,
            "",
            123,
            [],
            "vision",
            "team-rules",
        ]
        for name in adversarial_names:
            err = knowledge.doc_reset(name)
            assert err == "only a role can be reset"
            self.assertEqual(err, "only a role can be reset", f"doc_reset did not reject {name!r}")

    def test_role_playbook_traversal_rejected(self):
        """Invariant: role_playbook rejects traversal payloads, absolute paths, and non-slugs returning empty string."""
        assert knowledge.role_playbook("../../../etc/passwd") == ""
        assert knowledge.role_playbook("role:../../etc/passwd") == ""
        assert knowledge.role_playbook("../backend-developer") == ""
        assert knowledge.role_playbook("/backend-developer") == ""
        assert knowledge.role_playbook(123) == ""
        assert knowledge.role_playbook(None) == ""
        assert knowledge.role_playbook("123-developer") == ""
        assert knowledge.role_playbook("1-tester") == ""
        assert knowledge.role_playbook("0-backend-developer") == ""
        assert knowledge.role_playbook("9-security-developer") == ""
        assert knowledge.role_playbook("role:123-developer") == ""
        assert knowledge.role_playbook("role:1-tester") == ""
        assert knowledge.role_playbook("a" * 45 + "-developer") == ""

        adversarial_roles = [
            "../../../etc/passwd",
            "role:../../etc/passwd",
            "/etc/passwd",
            "role:/etc/passwd",
            "../backend-developer",
            "../../../roles/backend-developer",
            "/backend-developer",
            "backend-developer/../../etc/passwd",
            "..\\backend-developer",
            "123-developer",
            "1-tester",
            "0-backend-developer",
            "9-security-developer",
            "role:123-developer",
            "role:1-tester",
            "a" * 45 + "-developer",
            None,
            "",
            123,
            [],
        ]
        for role in adversarial_roles:
            result = knowledge.role_playbook(role)
            assert result == ""
            self.assertEqual(result, "", f"role_playbook did not reject {role!r}, got: {result[:50]!r}")

    def test_doc_get_traversal_rejected(self):
        """Invariant: doc_get returns None for traversal and invalid names."""
        assert knowledge.doc_get("../../../etc/passwd") is None
        assert knowledge.doc_get("role:../../etc/passwd") is None
        assert knowledge.doc_get("/etc/passwd") is None
        assert knowledge.doc_get(None) is None
        assert knowledge.doc_get(123) is None
        assert knowledge.doc_get("role:123-developer") is None

        adversarial_names = [
            "../../../etc/passwd",
            "role:../../etc/passwd",
            "/etc/passwd",
            "role:123-developer",
            None,
            "",
            123,
            [],
        ]
        for name in adversarial_names:
            assert knowledge.doc_get(name) is None
            self.assertIsNone(knowledge.doc_get(name), f"doc_get did not reject {name!r}")

    def test_path_confinement_defense_in_depth(self):
        """Invariant: doc_path stays confined within intended directory even if name or config attempts escape."""
        assert knowledge.doc_path("vision") is not None
        self.assertIsNotNone(knowledge.doc_path("vision"))

        old_vision = ctx.C.get("VISION_FILE", "docs/vision.md")
        try:
            ctx.C["VISION_FILE"] = "../../etc/passwd"
            assert knowledge.doc_path("vision") is None
            self.assertIsNone(knowledge.doc_path("vision"))
            self.assertIsNone(knowledge.doc_path("vision", write=True))
            self.assertIsNone(knowledge.doc_get("vision"))
            self.assertEqual(knowledge.doc_set("vision", "payload"), "unknown document")

            ctx.C["VISION_FILE"] = "/etc/passwd"
            assert knowledge.doc_path("vision") is None
            self.assertIsNone(knowledge.doc_path("vision"))
            self.assertIsNone(knowledge.doc_path("vision", write=True))

            ctx.C["VISION_FILE"] = "docs/../../etc/shadow"
            assert knowledge.doc_path("vision") is None
            self.assertIsNone(knowledge.doc_path("vision"))
            self.assertIsNone(knowledge.doc_path("vision", write=True))
        finally:
            ctx.C["VISION_FILE"] = old_vision

        assert knowledge.doc_path("vision") is not None
        self.assertIsNotNone(knowledge.doc_path("vision"))

    def test_legitimate_names_resolve_and_load(self):
        """Invariant: legitimate documents and roles resolve safely and stay inside intended roots."""
        assert knowledge.doc_path("vision") is not None
        assert knowledge.doc_path("team-rules") is not None
        assert knowledge.doc_path("role:backend-developer") is not None
        assert knowledge.doc_get("role:backend-developer") is not None
        assert knowledge.role_playbook("backend-developer") != ""
        assert knowledge.role_playbook("role:backend-developer") != ""

        # Standard docs
        self.assertIsNotNone(knowledge.doc_path("vision"))
        self.assertTrue(knowledge.doc_path("vision").endswith(ctx.C["VISION_FILE"]))
        self.assertIsNotNone(knowledge.doc_path("team-rules"))
        self.assertTrue(knowledge.doc_path("team-rules").endswith("team-rules.md"))
        self.assertIsNotNone(knowledge.doc_path("decisions"))
        self.assertTrue(knowledge.doc_path("decisions").endswith("decisions.md"))
        self.assertIsNotNone(knowledge.doc_path("lessons"))
        self.assertTrue(knowledge.doc_path("lessons").endswith("lessons.md"))

        # Role doc path
        role_path = knowledge.doc_path("role:backend-developer")
        self.assertIsNotNone(role_path)
        self.assertTrue(role_path.endswith(os.path.join("roles", "backend-developer.md")))

        write_path = knowledge.doc_path("role:backend-developer", write=True)
        self.assertIsNotNone(write_path)
        self.assertTrue(write_path.startswith(ctx.CFG_DIR))
        self.assertTrue(write_path.endswith(os.path.join("roles", "backend-developer.md")))

        # doc_get for existing role
        doc = knowledge.doc_get("role:backend-developer")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["name"], "role:backend-developer")
        self.assertIn("Backend", doc["text"])

        # role_playbook for legitimate names and variations
        self.assertTrue(knowledge.role_playbook("backend-developer").startswith("# Backend"))
        self.assertTrue(knowledge.role_playbook("role:backend-developer").startswith("# Backend"))
        self.assertTrue(knowledge.role_playbook("Backend Developer").startswith("# Backend"))
        self.assertTrue(knowledge.role_playbook("Security Tester").startswith("# Security"))
        self.assertTrue(knowledge.role_playbook("QA tester").startswith("# Tester"))
        self.assertEqual(knowledge.role_playbook("astronaut"), "")


if __name__ == "__main__":
    unittest.main()

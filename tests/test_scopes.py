import json
import os
from pathlib import Path
import unittest
import uuid

from hpa.core import Project, Blocked, select, memory_context, shadow, verify_capsule_dependency

ROOT = Path(__file__).resolve().parents[1]


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.a = Project.load(ROOT / "fixtures/A/project-capsule.json")
        self.b = Project.load(ROOT / "fixtures/B/project-capsule.json")
        self.binding = self.a.binding(uuid.uuid4().hex)

    def test_a_cannot_select_b_skill_or_tool(self):
        result = select(self.a, self.binding, "inspect meridian report")
        self.assertEqual(result["skills"], ["meridian"])
        self.assertEqual(result["tools"], ["read_file"])
        self.assertNotIn("cobalt", result["skills"])
        self.assertNotIn("search_files", result["tools"])
        with self.assertRaises(Blocked):
            select(self.a, self.binding, "inspect cobalt ledger")

    def test_missing_wrong_and_stale_session_blocked(self):
        for binding in (None, self.b.binding("wrong"), {**self.binding, "capsule_digest": "obsolete"}, {**self.binding, "root": str(self.b.root)}):
            with self.subTest(binding=binding), self.assertRaises(Blocked):
                select(self.a, binding, "inspect meridian report")

    def test_low_confidence_never_guesses(self):
        with self.assertRaises(Blocked):
            select(self.a, self.binding, "unrelated cooking question")
        self.assertEqual(self.binding["project_id"], "project-a")

    def test_ambiguous_equal_skills_blocked(self):
        # Both are legitimate A candidates, identical task evidence must clarify.
        data = dict(self.a.data, allowed_skills=["meridian", "meridian-twin"])
        capsule = self.a.artifacts / (uuid.uuid4().hex + "-capsule.json")
        data["project_root"] = ".."
        self.a.artifacts.mkdir(parents=True, exist_ok=True)
        capsule.write_text(json.dumps(data), encoding="utf-8")
        project = Project.load(capsule)
        with self.assertRaisesRegex(Blocked, "AMBIGUOUS"):
            select(project, project.binding("ambiguous"), "inspect meridian report")

    def test_artifact_escape_blocked(self):
        name = uuid.uuid4().hex + ".txt"
        path = self.a.write_artifact(self.binding, name, "synthetic A only")
        self.assertTrue(path.is_relative_to(self.a.artifacts))
        self.assertFalse((self.b.artifacts / name).exists())
        for target in ("../B/escape.txt", str(self.b.artifacts / name), "nested/../../escape.txt", "..\\escape.txt"):
            with self.subTest(target=target), self.assertRaises(Blocked):
                self.a.write_artifact(self.binding, target, "denied")

    def test_memory_a_excludes_b_and_global(self):
        context = memory_context(self.a, self.binding, "meridian", Path(os.environ["MEMORY_OS_ROOT"]), Path(os.environ["MEMORY_OS_PYTHON"]), self.a.root / "synthetic-memory.json")
        self.assertIn("A_MEMORY_MERIDIAN", context["context"])
        self.assertNotIn("B_MEMORY_COBALT", context["context"])
        self.assertNotIn("GLOBAL_MEMORY", context["context"])
        self.assertEqual(context["namespace"], "synthetic/project-a")
        other = memory_context(self.b, self.b.binding("b-memory"), "cobalt", Path(os.environ["MEMORY_OS_ROOT"]), Path(os.environ["MEMORY_OS_PYTHON"]), self.b.root / "synthetic-memory.json")
        self.assertIn("B_MEMORY_COBALT", other["context"])
        self.assertNotIn("A_MEMORY_MERIDIAN", other["context"])

    def test_private_or_other_project_store_blocked(self):
        for store in (self.b.root / "synthetic-memory.json", self.a.root / "private.db", self.a.root / "memory.json"):
            with self.assertRaises(Blocked):
                memory_context(self.a, self.binding, "test", ROOT, Path("python"), store)

    def test_dhaf_stays_shadow(self):
        result = shadow(self.a, self.binding, Path(os.environ["DHAF_ROOT"]))
        self.assertEqual(result["mode"], "SHADOW_ONLY")
        self.assertFalse(result["live_ready"])
        self.assertFalse(result["recommendation_executed"])

    def test_capsule_dependency_drift_blocked(self):
        from unittest.mock import patch
        import capsule
        verify_capsule_dependency()
        with patch.object(capsule, "__file__", str(self.a.artifacts / "fake-package" / "__init__.py")):
            with self.assertRaisesRegex(Blocked, "DEPENDENCY_DRIFT"):
                verify_capsule_dependency()

    def test_skill_tool_grant_cannot_expand_host_selection(self):
        data = dict(self.a.data, allowed_tools=["search_files"], project_root="..")
        capsule_file = self.a.artifacts / (uuid.uuid4().hex + "-capsule.json")
        self.a.artifacts.mkdir(parents=True, exist_ok=True)
        capsule_file.write_text(json.dumps(data), encoding="utf-8")
        project = Project.load(capsule_file)
        with self.assertRaisesRegex(Blocked, "GRANT_EXCEEDS"):
            select(project, project.binding("wrong-tool-grant"), "inspect meridian report")


if __name__ == "__main__":
    unittest.main()

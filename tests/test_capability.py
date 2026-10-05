"""Capability Router V0.1 — self-contained regression (no capsule pkg / Memory OS / DHAF)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hpa.core import Project
from hpa.capability import route_capabilities, CAP_ORDER


def _make_capsule(root: Path, pid, blocked, tools, now="2026-10-05T13:00:00+08:00") -> Path:
    """A capsule whose project_root points at the PARENT of root, so Project.root == root
    (the workspace). Placed under root so artifact_workspace stays beneath it."""
    (root / "work").mkdir(parents=True, exist_ok=True)
    p = root / "work" / "project-capsule.json"
    p.write_text(json.dumps(dict(
        version="0.1", project_id=pid, project_name="selftest",
        project_root="..", allowed_skills=["selftest"], allowed_tools=list(tools),
        memory_namespace="synthetic/" + pid, artifact_workspace="work/artifacts",
        created_at=now, blocked_capabilities=blocked)), encoding="utf-8")
    return p


class CapabilityRouterTests(unittest.TestCase):
    TOOLS = ["memory", "read_file", "search_files", "web_search", "web_extract", "execute_code"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # real workspace + capsule bound to project id 'p_x'
        self.cap_path = _make_capsule(
            self.root, "p_x",
            ["external_agent", "code_edit", "write_delete", "delete"],
            self.TOOLS)
        self.cap = Project.load(self.cap_path)
        self.project = SimpleNamespace(id="p_x", project_id="p_x",
                                       workspace=str(self.root), primary_path=str(self.root))

    def dec(self, text, tools=None, project=None, capsule=None, adapters=("H_NATIVE", "CX")):
        return route_capabilities(text, project if project is not None else self.project,
                                  capsule if capsule is not None else self.cap,
                                  self.TOOLS if tools is None else tools,
                                  available_adapters=adapters)

    def _v(self, d, cap):
        return d.verdicts[cap]["status"]

    # --- the six required task classes ---
    def test_a_project_state(self):
        d = self.dec("我们这个项目目前做到哪一步？")
        self.assertEqual(self._v(d, "PROJECT_CONTEXT"), "ALLOW")
        self.assertEqual(self._v(d, "WEB_SEARCH"), "NOT_NEEDED")

    def test_b_memory_recall(self):
        d = self.dec("回忆我们之前对 Memory OS 的关键决策。")
        self.assertEqual(self._v(d, "MEMORY"), "ALLOW")
        self.assertEqual(self._v(d, "WEB_SEARCH"), "NOT_NEEDED")

    def test_c_web_latest(self):
        d = self.dec("查一下 Hermes 最新公开版本信息。")
        self.assertEqual(self._v(d, "WEB_SEARCH"), "ALLOW")
        self.assertEqual(self._v(d, "PROJECT_CONTEXT"), "NOT_NEEDED")

    def test_d_local_read(self):
        d = self.dec("读取当前项目 README 并总结当前架构。")
        self.assertEqual(self._v(d, "LOCAL_READ"), "ALLOW")
        self.assertEqual(self._v(d, "PROJECT_CONTEXT"), "ALLOW")
        self.assertEqual(self._v(d, "WEB_SEARCH"), "NOT_NEEDED")

    def test_e_external_agent_blocked(self):
        d = self.dec("让 CX 帮我改一下代码。")
        self.assertIn("external_agent", d.requested_restricted)
        self.assertIn("code_edit", d.requested_restricted)
        self.assertEqual(d.requested_restricted["code_edit"]["status"], "BLOCK")
        self.assertEqual(d.selected, [])
        self.assertFalse(d.execution_attempted)

    def test_f_delete_blocked(self):
        d = self.dec("删除项目里的测试文件。")
        self.assertIn("write_delete", d.requested_restricted)
        self.assertEqual(d.requested_restricted["write_delete"]["status"], "BLOCK")
        self.assertFalse(d.execution_attempted)

    # --- acceptance criteria ---
    def test_allow_block_precedence(self):
        # capsule blocks external_agent even though the tool/adapter is available
        d = self.dec("让 CX 帮我改一下代码。")
        self.assertEqual(d.requested_restricted["external_agent"]["status"], "BLOCK")
        self.assertEqual(d.requested_restricted["code_edit"]["status"], "BLOCK")

    def test_unavailable_distinct_from_block(self):
        # drop the memory tool: task-relevant MEMORY becomes UNAVAILABLE (env), not BLOCK
        tools = [t for t in self.TOOLS if t != "memory"]
        d = self.dec("回忆我们之前对 Memory OS 的关键决策。", tools=tools)
        self.assertEqual(self._v(d, "MEMORY"), "UNAVAILABLE")
        # blocked stays BLOCK regardless of tool availability
        d2 = self.dec("删除项目里的测试文件。", tools=tools)
        self.assertEqual(d2.requested_restricted["write_delete"]["status"], "BLOCK")

    def test_no_unneeded_web(self):
        for text in ("我们这个项目目前做到哪一步？", "读取当前项目 README 并总结当前架构。"):
            d = self.dec(text)
            self.assertEqual(self._v(d, "WEB_SEARCH"), "NOT_NEEDED")

    def test_project_scope_isolation(self):
        # outside the project space the capsule must NOT drive routing
        d = route_capabilities("读取当前项目 README 并总结当前架构。",
                               project=None, capsule=None, runtime_tools=self.TOOLS,
                               available_adapters=("H_NATIVE", "CX"))
        self.assertFalse(d.in_scope)
        self.assertEqual(d.verdicts["PROJECT_CONTEXT"]["status"], "NOT_NEEDED")

    def test_blocked_capability_not_executed(self):
        for text in ("让 CX 帮我改一下代码。", "删除项目里的测试文件。"):
            d = self.dec(text)
            self.assertFalse(d.execution_attempted)


if __name__ == "__main__":
    unittest.main()

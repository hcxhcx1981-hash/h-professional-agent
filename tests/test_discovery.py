"""Runtime Capability Discovery V0.1 — self-contained (no Hermes checkout).

Covers the discovery adapter's facts + the acceptance criteria:
session-preferred presence, REGISTERED vs SESSION vs AVAILABLE semantics,
capsule-block precedence over a present tool, simulated missing tool ->
UNAVAILABLE without fallback, project-scope isolation, and no execution.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hpa.core import Project
from hpa.capability import (discover_runtime_tools, route_capabilities,
                           ToolDiscovery, CAPABILITIES, CAP_ORDER)

# All six mapped tool names (search_files is a mapped tool but is not the
# capability tool of any of the six; the capability tool set is 5 after dedupe).
MAPPED = ["memory", "read_file", "search_files", "web_search", "web_extract", "execute_code"]
# What runtime_tools() can ever return: the distinct capability-mapped tools.
CAP_TOOLS = sorted({c["tool"] for c in CAPABILITIES.values()})


def _capsule(root: Path, pid, blocked, allowed, now="2026-10-05T13:00:00+08:00") -> Path:
    (root / "work").mkdir(parents=True, exist_ok=True)
    p = root / "work" / "project-capsule.json"
    p.write_text(json.dumps(dict(
        version="0.1", project_id=pid, project_name="selftest",
        project_root="..", allowed_skills=["selftest"], allowed_tools=allowed,
        memory_namespace="synthetic/" + pid, artifact_workspace="work/artifacts",
        created_at=now, blocked_capabilities=blocked)), encoding="utf-8")
    return p


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # A real runtime whose session requests every mapped tool.
        self.disc = discover_runtime_tools(injected_session=frozenset(MAPPED),
                                          injected_registered=frozenset(MAPPED))

    def _proj(self):
        return SimpleNamespace(id="p_x", project_id="p_x",
                               workspace=str(self.root), primary_path=str(self.root))

    def _cap(self, blocked=(), allowed=None):
        allowed = MAPPED if allowed is None else allowed
        p = _capsule(self.root, "p_x", list(blocked), list(allowed))
        return Project.load(p)

    def _route(self, text, disc=None, project=None, capsule=None):
        return route_capabilities(text, project if project is not None else self._proj(),
                                  capsule if capsule is not None else self._cap(),
                                  (disc or self.disc).runtime_tools(),
                                  available_adapters=("H_NATIVE", "CX"))

    def _v(self, d, cap):
        return d.verdicts[cap]["status"]

    # --- the discovery facts themselves -------------------------------
    def test_session_preferred_not_degraded(self):
        d = self.disc
        self.assertFalse(d.degraded)
        self.assertEqual(d.session_count, len(MAPPED))
        self.assertEqual(d.runtime_tools(), CAP_TOOLS)

    def test_registered_vs_available_semantics(self):
        # Registered-but-not-session-requested feeds nothing to the preferred set:
        reg_only = discover_runtime_tools(injected_registered=frozenset(MAPPED))
        self.assertTrue(reg_only.degraded)          # no session source -> registered discovery
        # A registration-only result must never be described as runtime-available:
        self.assertNotIn("available", reg_only.source.lower())
        self.assertIn("health=UNKNOWN", reg_only.source)
        self.assertEqual(reg_only.runtime_tools(), CAP_TOOLS)  # degraded falls back to registered
        # Health is NEVER confirmed.
        self.assertEqual(self.disc.health_status, "UNKNOWN")
        self.assertFalse(self.disc.probed)

    def test_no_probe_no_execution(self):
        # A blocked/absent tool is never probed: the discovery never runs check_fn.
        self.assertFalse(self.disc.probed)
        d = self._route("查一下 Hermes 最新公开版本信息。")
        self.assertFalse(d.execution_attempted)

    # --- acceptance criteria -----------------------------------------
    def test_capsule_block_precedence(self):
        # web_search is present in the session set, yet a capsule that blocks the
        # WEB_SEARCH capability wins: BLOCK, not ALLOW (precedence over presence).
        d = self._route("查一下 Hermes 最新公开版本信息。",
                        capsule=self._cap(blocked=["WEB_SEARCH"]))
        self.assertEqual(self._v(d, "WEB_SEARCH"), "BLOCK")

    def test_missing_tool_unavailable_no_fallback(self):
        # Simulate a runtime where web_search is absent -> UNAVAILABLE, and no
        # other web/local capability is silently substituted for it.
        d = self._route("查一下 Hermes 最新公开版本信息。",
                        disc=self.disc.without({"web_search"}))
        self.assertEqual(self._v(d, "WEB_SEARCH"), "UNAVAILABLE")
        self.assertNotIn("WEB_SEARCH", d.selected)
        self.assertEqual(d.selected, [])  # nothing falls back to another tool

    def test_project_scope_isolation(self):
        d = route_capabilities("读取当前项目 README 并总结当前架构。",
                               project=None, capsule=None,
                               runtime_tools=self.disc.runtime_tools(),
                               available_adapters=("H_NATIVE", "CX"))
        self.assertFalse(d.in_scope)
        self.assertEqual(d.verdicts["PROJECT_CONTEXT"]["status"], "NOT_NEEDED")

    def test_no_unneeded_web_or_memory(self):
        d = self._route("我们这个项目目前做到哪一步？")
        self.assertEqual(self._v(d, "WEB_SEARCH"), "NOT_NEEDED")
        d2 = self._route("读取当前项目 README 并总结当前架构。")
        self.assertEqual(self._v(d2, "WEB_SEARCH"), "NOT_NEEDED")


if __name__ == "__main__":
    unittest.main()

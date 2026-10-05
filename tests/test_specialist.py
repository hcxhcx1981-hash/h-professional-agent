"""Specialist Agent V0.1 unit tests (offline, fixture-based; no Hermes I/O).

Covers: single-specialist selection, capsule/project mismatch, forbidden
capability block, cross-project evidence isolation, determinism (stateless),
Main H aggregation seam, and unregistered-specialist refusal.
"""
import unittest
from types import SimpleNamespace

from hpa.core import Blocked
from hpa.specialist import (REGISTRY, aggregate_result, dispatch,
                            market_regulation_evidence_specialist,
                            register, select_specialist, SpecialistTask)


class _Capsule:
    """Minimal duck-type matching hpa.core.Project (id / root / data)."""

    def __init__(self, pid, root, data=None):
        self.id = pid
        self.root = root
        self.data = data or {"project_name": "market-regulation-agent",
                             "phase": "competition-closeout-readonly-audit",
                             "allowed_tools": ["read_file", "execute_code", "memory"],
                             "blocked_capabilities": ["memory_write"]}


def _state(pid="p_d1bcd5f2", name="market-regulation-agent", ws="C:/proj/mra"):
    return SimpleNamespace(project_id=pid, project_name=name,
                           workspace=ws, aliases=(), device_id=None,
                           activity_at=())


CLAIMS = [
    {"claim_id": "C1", "statement": "防鼠板场景已有结构化判定证据并闭环",
     "min_independent": 2},
    {"claim_id": "C2", "statement": "视频识别能力已验证", "min_independent": 2},
]
RECORDS = [
    {"claim_id": "C1", "source_id": "L1", "source_url": "docs/CAPABILITY_MATRIX.md",
     "extract": "Evidence Gate", "supports": "YES", "asserts": "gate_DONE"},
    {"claim_id": "C1", "source_id": "L2", "source_url": "README.md",
     "extract": "Sprint C", "supports": "YES", "asserts": "gate_DONE"},
    {"claim_id": "C2", "source_id": "L3", "source_url": "README.md",
     "extract": "NOT_YET_VERIFIED", "supports": "YES"},
    {"claim_id": "C2", "source_id": "L4", "source_url": "docs/S5_VIDEO.md",
     "extract": "NOT_YET_VERIFIED", "supports": "NO", "asserts": "video_NOT_VERIFIED"},
]

TASK = {
    "project_id": "p_d1bcd5f2", "task_id": "t-verify-001",
    "specialist_id": "market-regulation-evidence-specialist",
    "user_intent": "核验当前市场监管项目中监管判断的证据是否充分，并指出冲突或证据不足",
    "scoped_context": {"claims": CLAIMS, "records": RECORDS},
    "allowed_capabilities": ("LOCAL_READ", "LOCAL_ANALYSIS"),
    "forbidden_capabilities": ("memory_write", "agent_reach", "external_agent",
                               "write_delete", "code_edit"),
}


class SpecialistSelectionTests(unittest.TestCase):
    def test_only_one_specialist_registered(self):
        # Clean up any probe registration from other tests first.
        REGISTRY.pop("x", None)
        self.assertEqual(set(REGISTRY), {"market-regulation-evidence-specialist"})

    def test_unregistered_id_is_blocked(self):
        with self.assertRaises(Blocked) as cm:
            select_specialist("some-other-specialist")
        self.assertIn("SPECIALIST_NOT_REGISTERED", str(cm.exception))

    def test_double_registration_refused(self):
        register("x", lambda t: {})
        try:
            with self.assertRaises(Blocked):
                register("x", lambda t: {})
        finally:
            REGISTRY.pop("x", None)

    def test_duplicate_register_same_id_refused(self):
        with self.assertRaises(Blocked):
            register("market-regulation-evidence-specialist", lambda t: {})


class SpecialistExecutionTests(unittest.TestCase):
    def test_c2_conflict_c1_verified(self):
        from hpa.specialist import market_regulation_evidence_specialist as run
        from hpa.specialist import SpecialistTask
        task = SpecialistTask(**TASK)
        r = run(task)
        self.assertEqual(r["status"], "COMPLETED")
        self.assertEqual(r["confidence"]["C1"], "high")
        self.assertEqual(r["confidence"]["C2"], "none")
        self.assertEqual(r["overall_claim_status"], "CONFLICT")
        self.assertTrue(r["conflicts"])
        self.assertIn("Main H / Human Review", r["recommended_next_action"])

    def test_run_is_pure_and_deterministic(self):
        # Stateless: two runs over identical input give the same DECISION
        # fields. The auto-stamped retrieved_at timestamp differs between
        # calls (wall clock), so determinism is asserted on the verdicts,
        # findings, conflicts and confidence — the decision content.
        task = SpecialistTask(**TASK)
        r1 = market_regulation_evidence_specialist(task)
        r2 = market_regulation_evidence_specialist(task)
        for key in ("status", "findings", "confidence", "conflicts",
                    "limitations", "recommended_next_action",
                    "overall_claim_status"):
            self.assertEqual(r1[key], r2[key], key)

    def test_foreign_record_is_rejected(self):
        from hpa.specialist import SpecialistTask
        bad = dict(TASK)
        bad["scoped_context"] = {"claims": CLAIMS, "records": RECORDS + [
            {"claim_id": "C9", "source_id": "X", "source_url": "../outside.md",
             "extract": "x"}]}
        with self.assertRaises(Blocked) as cm:
            market_regulation_evidence_specialist(SpecialistTask(**bad))
        self.assertIn("EVIDENCE_RECORD_CLAIM_MISMATCH", str(cm.exception))

    def test_missing_contract_field_blocked(self):
        from hpa.specialist import SpecialistTask
        bad = {k: v for k, v in TASK.items() if k != "task_id"}
        with self.assertRaises(Blocked) as cm:
            SpecialistTask.from_dict(bad)
        self.assertIn("SPECIALIST_TASK_FIELD_MISSING", str(cm.exception))


class DispatchGateTests(unittest.TestCase):
    def _capsule(self, pid="p_d1bcd5f2", root="C:/proj/mra"):
        from pathlib import Path
        return _Capsule(pid, Path(root))

    def test_cross_project_dispatch_blocked(self):
        # Specialist context claims a different project than the capsule:
        # the dispatch must not hand project files to a foreign task.
        task = dict(TASK)
        task["project_id"] = "p_OTHER"
        project = _state()
        with self.assertRaises(Blocked) as cm:
            dispatch(task, project=project, capsule=self._capsule(),
                     runtime_tools=set(), available_adapters=("H_NATIVE", "CX"),
                     round_blocked=())
        self.assertIn("PROJECT_MISMATCH", str(cm.exception))

    def test_forbidden_request_blocked_before_execution(self):
        # Task requests a memory write + external agent: router classifies
        # write_delete/external_agent as requested restricted; the
        # forbidden intersection must BLOCK the specialist run.
        task = dict(TASK)
        task["user_intent"] = ("核验当前市场监管项目证据，同时改代码并调用外部 agent "
                               "把结果写回 memory 与文件")
        project = _state()
        with self.assertRaises(Blocked) as cm:
            dispatch(task, project=project, capsule=self._capsule(),
                     runtime_tools={"read_file", "execute_code", "memory"},
                     available_adapters=("H_NATIVE", "CX"), round_blocked=())
        self.assertIn("FORBIDDEN_CAPABILITY_REQUESTED", str(cm.exception))

    def test_clean_task_runs_and_aggregates_to_main(self):
        project = _state()
        cap = self._capsule()
        from hpa.specialist import dispatch as d
        task = dict(TASK)
        r = d(task, project=project, capsule=cap,
              runtime_tools={"read_file", "execute_code", "memory"},
              available_adapters=("H_NATIVE", "CX"), round_blocked=())
        self.assertEqual(r["contract_version"], "specialist.v0.1")
        self.assertFalse(r["router_gate"]["execution_attempted"])
        t = SpecialistTask(**task)
        agg = aggregate_result({"summary": "由 Main H 汇总：C1 证据充分，C2 存在冲突"}, r, t)
        self.assertEqual(agg["final_answer_source"], "MAIN_AGENT")
        self.assertTrue(agg["human_review_required"])
        self.assertIn("specialist_result", agg)

    def test_evidence_path_escape_blocked(self):
        from hpa.specialist import gather_local_evidence
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.md").write_text("ok")
            with self.assertRaises(Blocked) as cm:
                gather_local_evidence(root, [
                    {"claim_id": "C1", "source_id": "EVIL",
                     "file": "../outside.md", "extract": "x"}])
            self.assertIn("EVIDENCE_PATH_OUTSIDE_PROJECT", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

"""Specialist Agent V0.1 — one registered specialist, pure in-process execution.

Minimal, reversible design on top of the existing layers (no new agent
runtime, no message queue, no database, no external dependency):

  official projects_db (read-only) -> project identity
  Project Capsule (hpa.core.Project.load)   -> (project_id, root) binding
  Capability Router (hpa.capability)        -> capability gate before execution
  this module                               -> specialist SELECTION + pure EXECUTION
  Evidence Verification (hpa.evidence)      -> claim verdicts inside the specialist

Rules frozen by the V0.1 task:
  - exactly ONE specialist is registered; dispatch of any other id is
    BLOCKED (no multi-specialist platform, no recursive delegation);
  - the specialist body is PURE: no I/O inside `run`. All project file
    reads are done by the ORCHESTRATOR (dispatch) under capsule scope
    checks and injected as `scoped_context`; the specialist only judges;
  - stateless by default: `run` has no persistence, no memory, no
    session state; two calls with the same input give the same output;
  - forbidden capabilities requested by the task are classified by the
    capability router and BLOCKED before the specialist runs;
  - the structured result ALWAYS returns to the caller (Main H) — the
    specialist is never a top-level agent (see aggregate_result()).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .capability import route_capabilities, detect_restricted
from .core import Blocked
from .evidence import build_record, verify_claim, to_bundle_json, VERIFIED, CONFLICT

SPECIALIST_CONTRACT_VERSION = "specialist.v0.1"

#: V0.1 registers exactly one specialist. Dispatching an unregistered id
#: is BLOCKED_Specialist_NOT_REGISTERED, not a silent fallback.
REGISTRY: dict[str, Callable[["SpecialistTask"], dict]] = {}


@dataclass(frozen=True)
class SpecialistTask:
    """Minimal input contract (frozen field set; nothing extra in V0.1)."""
    project_id: str
    task_id: str
    specialist_id: str
    user_intent: str
    scoped_context: dict
    allowed_capabilities: tuple[str, ...]
    forbidden_capabilities: tuple[str, ...]

    @classmethod
    def from_dict(cls, d: dict) -> "SpecialistTask":
        required = {"project_id", "task_id", "specialist_id", "user_intent",
                    "scoped_context", "allowed_capabilities", "forbidden_capabilities"}
        missing = required - set(d)
        if missing:
            raise Blocked(f"SPECIALIST_TASK_FIELD_MISSING:{sorted(missing)}")
        return cls(
            project_id=str(d["project_id"]), task_id=str(d["task_id"]),
            specialist_id=str(d["specialist_id"]), user_intent=str(d["user_intent"]),
            scoped_context=d["scoped_context"],
            allowed_capabilities=tuple(d["allowed_capabilities"]),
            forbidden_capabilities=tuple(d["forbidden_capabilities"]),
        )


def register(specialist_id: str, fn: Callable[["SpecialistTask"], dict]) -> None:
    if specialist_id in REGISTRY:
        raise Blocked(f"SPECIALIST_ALREADY_REGISTERED:{specialist_id}")
    REGISTRY[specialist_id] = fn


def select_specialist(specialist_id: str) -> Callable[["SpecialistTask"], dict]:
    try:
        return REGISTRY[specialist_id]
    except KeyError:
        raise Blocked(f"SPECIALIST_NOT_REGISTERED:{specialist_id}")


# --------------------------------------------------------------------------
# The single registered specialist: market-regulation evidence specialist.
#
# Narrow duty (frozen): given scoped LOCAL_FILE evidence records about a
# set of regulatory claims, produce per-claim verdicts via the existing
# pure Evidence Verification layer. It reads nothing itself, writes
# nothing, cannot call other specialists, and never issues a final
# enforcement decision — verdicts are evidence-confidence judgments only.
# --------------------------------------------------------------------------

def _confidence(status: str) -> str:
    return {"VERIFIED": "high", "INSUFFICIENT": "low"}.get(status, "none")


def market_regulation_evidence_specialist(task: SpecialistTask) -> dict:
    ctx = task.scoped_context
    if not isinstance(ctx, dict) or not ctx:
        raise Blocked("SCOPED_CONTEXT_EMPTY")
    claims = ctx.get("claims")
    records = ctx.get("records")
    if not isinstance(claims, list) or not claims or not isinstance(records, list):
        raise Blocked("SCOPED_CONTEXT_SHAPE_INVALID")
    # Every record must belong to this task's claims — no foreign data in.
    claim_ids = {c["claim_id"] for c in claims}
    for r in records:
        if r.get("claim_id") not in claim_ids:
            raise Blocked("EVIDENCE_RECORD_CLAIM_MISMATCH")
    # Record ids scoped to each claim; independence inside one project repo
    # counts by distinct source path (host = LOCAL_FILE path), per V0.1 rule.
    by_claim: dict[str, list] = {}
    for r in records:
        rec = build_record(
            claim_id=r["claim_id"], source_id=r["source_id"],
            source_type=r.get("source_type", "LOCAL_FILE"),
            evidence_text=r.get("evidence_text", ""),
            supports=r.get("supports", "PARTIAL"),
            source_url=r.get("source_url", ""),
            published_at=r.get("published_at"),
            retrieved_at=r.get("retrieved_at"),
            conflict_group=r.get("conflict_group"),
            asserts=r.get("asserts"),
            source_quality=r.get("source_quality", "high"),
        )
        by_claim.setdefault(r["claim_id"], []).append(rec)

    findings, conflicts, limitations = [], [], []
    verdicts: dict[str, Any] = {}
    for c in claims:
        bundle = verify_claim(
            claim_id=c["claim_id"], statement=c["statement"],
            records=by_claim.get(c["claim_id"], []),
            min_independent=int(c.get("min_independent", 2)),
        )
        verdicts[c["claim_id"]] = bundle
        findings.append(f"{c['claim_id']}: {bundle.status} ({bundle.rationale})")
        if bundle.status == CONFLICT:
            conflicts.extend(bundle.conflicts)
        elif bundle.status != VERIFIED:
            limitations.append(f"{c['claim_id']}: {bundle.rationale}")

    statuses = [b.status for b in verdicts.values()]
    overall = CONFLICT if CONFLICT in statuses else VERIFIED if all(s == VERIFIED for s in statuses) else "INSUFFICIENT"
    status = "COMPLETED"
    return {
        "specialist_id": task.specialist_id,
        "status": status,
        "findings": findings,
        "evidence": [to_bundle_json(b) for b in verdicts.values()],
        "confidence": {cid: _confidence(b.status) for cid, b in verdicts.items()},
        "conflicts": sorted(set(conflicts)),
        "limitations": limitations,
        "recommended_next_action": (
            "Main H / Human Review 层汇总并决定后续；本 Specialist 不直接给出最终监管决定"
            if conflicts or overall != "VERIFIED" else
            "证据充分且无冲突；由 Main H 汇总后可进入下一步任务"
        ),
        "overall_claim_status": overall,
    }


register("market-regulation-evidence-specialist", market_regulation_evidence_specialist)


# --------------------------------------------------------------------------
# Dispatch: the orchestration seam. All project-file I/O happens HERE,
# under capsule scope checks — the specialist body stays pure.
# --------------------------------------------------------------------------

def gather_local_evidence(project_root: Path, specs: list[dict]) -> list[dict]:
    """Read scoped project files ONLY (read-only; containment enforced).

    `specs` entries: claim_id, file (project-relative), extract (substring),
    supports, asserts, source_id, conflict_group, source_quality. The record's
    evidence_text is the VERBATIM substring found in the file — the caller
    cannot fabricate evidence without the file containing it.
    """
    root = project_root.resolve()
    out: list[dict] = []
    for s in specs:
        rel = Path(s["file"])
        if rel.is_absolute() or ".." in rel.parts:
            raise Blocked(f"EVIDENCE_PATH_OUTSIDE_PROJECT:{s['file']}")
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise Blocked(f"EVIDENCE_FILE_MISSING:{s['file']}")
        text = target.read_text(encoding="utf-8", errors="replace")
        needle = s["extract"]
        if needle not in text:
            raise Blocked(f"EVIDENCE_EXTRACT_NOT_FOUND:{s['file']}::{s['source_id']}")
        out.append({
            "claim_id": s["claim_id"], "source_id": s["source_id"],
            "source_type": "LOCAL_FILE", "source_url": rel.as_posix(),
            "evidence_text": needle, "supports": s.get("supports", "PARTIAL"),
            "published_at": s.get("published_at"),
            "conflict_group": s.get("conflict_group"),
            "asserts": s.get("asserts"),
            "source_quality": s.get("source_quality", "high"),
        })
    return out


def aggregate_result(main_facing: dict, specialist_result: dict, task: "SpecialistTask") -> dict:
    """Main H aggregation seam: the user-facing result is assembled HERE.

    The specialist's structured result is embedded, never promoted to the
    top level: this is the single authoritative answer shape.
    """
    return {
        "contract_version": SPECIALIST_CONTRACT_VERSION,
        "task_id": task.task_id,
        "project_id": task.project_id,
        "specialist_id": task.specialist_id,
        "status": "AGGREGATED_BY_MAIN_AGENT",
        "final_answer_source": "MAIN_AGENT",
        "specialist_result": specialist_result,
        "human_review_required": bool(specialist_result.get("conflicts")) or specialist_result.get("overall_claim_status") != "VERIFIED",
        **main_facing,
    }


def dispatch(task: dict, *, project, capsule,
             runtime_tools: Iterable, available_adapters: tuple,
             round_blocked: tuple, evidence_specs: list[dict] | None = None) -> dict:
    """Main H -> specialist handoff, gated by the existing layers.

    1. capability router verdicts on the task text (gate BEFORE execution);
    2. forbidden/restricted requests are classified and BLOCKED;
    3. scoped evidence is gathered by THIS orchestrator (read-only,
       capsule-contained); the specialist body runs pure on the result;
    4. the structured result returns here for Main H aggregation.
    """
    t = SpecialistTask.from_dict(task)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", t.task_id):
        raise Blocked("TASK_ID_INVALID")
    cap_id = getattr(capsule, "id", None)
    if cap_id is not None and t.project_id != cap_id:
        raise Blocked(f"PROJECT_MISMATCH:{t.project_id}!={cap_id}")
    run = select_specialist(t.specialist_id)

    # Capability gate: the router is the single source of capability policy.
    blocked_round = set(round_blocked)
    blocked_round |= set(t.forbidden_capabilities)
    # Anything the task itself requests that is forbidden in this round is
    # classified (never executed) — and blocks the specialist run.
    router = route_capabilities(t.user_intent, project, capsule, runtime_tools,
                                available_adapters=available_adapters,
                                round_blocked=blocked_round)
    requested_restricted = set(router.requested_restricted)
    # 1) Task requested a restricted capability that this round forbids.
    if requested_restricted & set(t.forbidden_capabilities):
        raise Blocked(f"FORBIDDEN_CAPABILITY_REQUESTED:{sorted(requested_restricted & set(t.forbidden_capabilities))}")
    # 2) Task requested a forbidden capability that the task text itself did
    #    not trigger restricted detection for: if the router nevertheless
    #    ALLOWed that capability, granting it would violate the round's
    #    forbidden list — block instead of silently under-enforcing.
    granted = [c for c, v in router.verdicts.items() if v["status"] == "ALLOW"]
    grant_violation = set(granted) & set(t.forbidden_capabilities)
    if grant_violation:
        raise Blocked(f"FORBIDDEN_CAPABILITY_GRANTED:{sorted(grant_violation)}")

    # Scoped context: project identity + evidence gathered by the orchestrator.
    ctx = t.scoped_context
    ctx["project_id"] = t.project_id
    ctx["project_name"] = capsule.data["project_name"]
    ctx["phase"] = capsule.data.get("phase")
    if evidence_specs:
        ctx["records"] = gather_local_evidence(capsule.root, evidence_specs)
    if "claims" not in ctx:
        raise Blocked("SCOPED_CONTEXT_CLAIMS_MISSING")

    result = run(t)
    result["router_gate"] = {
        "selected": router.selected, "blocked": router.blocked,
        "unavailable": router.unavailable, "not_needed": router.not_needed,
        "requested_restricted": sorted(requested_restricted),
        "execution_attempted": router.execution_attempted,
    }
    result["contract_version"] = SPECIALIST_CONTRACT_VERSION
    return result

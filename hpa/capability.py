"""Minimal Capability Router V0.1 — READ-ONLY verdicts; it NEVER executes a tool.

Reuses the existing layer, not a second routing system:
  - hpa.continuation.resolve_capsule  (project -> capsule binding, (id, root) criteria)
  - hpa.continuation.infer_intent      (task intent, unchanged)
  - hpa.continuation._project_identity (duck-typed Hermes/internal project id+workspace)
  - hpa.core.Project                   (capsule load; allowed_tools / blocked_capabilities)

The six candidate capabilities map to REAL Hermes builtin tool names (from
tools/*.py), not invented synonyms:

    MEMORY          -> memory
    PROJECT_CONTEXT -> read_file   (reads the bound project's docs; requires scope)
    WEB_SEARCH      -> web_search
    WEB_EXTRACT     -> web_extract
    LOCAL_READ      -> read_file
    LOCAL_ANALYSIS  -> execute_code

Verdicts (per capability): ALLOW / BLOCK / NOT_NEEDED / UNAVAILABLE.
  - NOT_NEEDED     : task is not relevant to this capability (nothing loaded).
  - BLOCK          : the project capsule (blocked_capabilities) or this round's
                     policy explicitly forbids it — precedence over model choice
                     and over tool availability.
  - UNAVAILABLE    : the capability's real tool/adapter is not available in the
                     current environment (distinct from BLOCK: the environment,
                     not a policy, denies it).
  - ALLOW          : task-relevant, not blocked, tool available and (if the
                     capsule constrains it) inside allowed_tools.

The router is decision-only: EXECUTION_ATTEMPTED is always False here. It is a
recommendation layer, not an executor (same stance as continuation.recommend:
READ_ONLY_RECOMMENDATION; EXECUTION/HEALTH_NOT_VALIDATED).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .continuation import _project_identity, infer_intent, resolve_capsule

# Candidate H-internal capabilities -> their REAL Hermes tool + task signals.
CAPABILITIES = {
    "MEMORY":          {"tool": "memory",       "needs_web": False},
    "PROJECT_CONTEXT": {"tool": "read_file",    "needs_web": False, "needs_project": True},
    "WEB_SEARCH":      {"tool": "web_search",   "needs_web": True},
    "WEB_EXTRACT":     {"tool": "web_extract",  "needs_web": True},
    "LOCAL_READ":      {"tool": "read_file",    "needs_web": False},
    "LOCAL_ANALYSIS":  {"tool": "execute_code", "needs_web": False},
}
CAP_ORDER = list(CAPABILITIES)  # stable output order

# Restricted / external capabilities a task may REQUEST; V0.1 classifies them, never executes.
RESTRICTED = ("external_agent", "code_edit", "write_delete")


def task_profile(text: str) -> set[str]:
    """Which of the six candidate capabilities a task is relevant to.

    Deliberate, narrow signals so UNRELATED capabilities stay NOT_NEEDED rather
    than being loaded by default. English + Chinese keywords; case-insensitive."""
    t = text.casefold()
    prof = set()

    def has(*kws: str) -> bool:
        return any(k in t for k in kws)

    # MEMORY: recalling this project's / our past decisions, state, progress.
    if has("回忆", "之前", "关键决策", "决策", "记得", "进展", "做到哪", "做到什么",
           "remember", "recall", "我们之前", "prior", "decision"):
        prof.add("MEMORY")
    # PROJECT_CONTEXT: asking about THIS / our current project's state.
    if has("这个项目", "我们的项目", "当前项目", "本项目", "目前", "this project",
           "current project", "项目状态", "项目目前", "项目做到"):
        prof.add("PROJECT_CONTEXT")
    # WEB_SEARCH: external / public / latest info that must be looked up online.
    if has("最新版本", "最新公开", "公开版本", "最新信息", "官网", "网上", "外部信息",
           "查一下", "search", "public version", "latest", "online", "hermes 最新"):
        prof.add("WEB_SEARCH")
    # WEB_EXTRACT: fetch / extract a specific web page / url / doc page.
    if has("抓取", "网页", "url", "http", "提取网页", "read the page", "extract"):
        prof.add("WEB_EXTRACT")
    # LOCAL_READ: read files inside THIS project (README, code, architecture).
    if has("读取当前项目", "readme", "架构", "总结当前", "看代码", "本地读",
           "project readme", "current architecture", "read the project"):
        prof.add("LOCAL_READ")
    # LOCAL_ANALYSIS: run local analysis / compute / diagnose on project data.
    if has("分析", "统计", "计算", "诊断", "local analysis", "compute"):
        prof.add("LOCAL_ANALYSIS")
    return prof


def detect_restricted(text: str) -> set[str]:
    """Restricted capabilities a task requests. Detected, classified — never executed."""
    t = text.casefold()
    found = set()

    def has(*kws: str) -> bool:
        return any(k in t for k in kws)

    if has("cx", "codex", "workbuddy", "外部 agent", "外部代理", "external agent",
           "agent 帮我", "让 cx", "让 codex"):
        found.add("external_agent")
    if has("改代码", "修改代码", "帮我改", "改一下代码", "开发", "修复", "重构",
           "edit the code", "change the code"):
        found.add("code_edit")
    if has("删除", "删掉", "删了", "清空", "移除文件", "delete", "remove the",
           "rm ", "去掉测试文件", "删除测试"):
        found.add("write_delete")
    return found


@dataclass(frozen=True)
class CapabilityVerdict:
    capability: str
    status: str
    reason: str
    tool: Optional[str] = None


@dataclass
class CapabilityDecision:
    task: str
    intent: str
    project: Optional[str]
    capsule: Optional[str]
    in_scope: bool
    verdicts: dict
    selected: list
    blocked: list
    unavailable: list
    not_needed: list
    requested_restricted: dict
    execution_attempted: bool
    policy_version: str = "capability-router.v0.1"

    def to_dict(self) -> dict:
        return {
            "TASK": self.task, "INTENT": self.intent,
            "PROJECT": self.project, "CAPSULE": self.capsule,
            "IN_SCOPE": self.in_scope,
            "VERDICTS": self.verdicts,
            "SELECTED_CAPABILITIES": self.selected,
            "BLOCKED_CAPABILITIES": self.blocked,
            "UNAVAILABLE_CAPABILITIES": self.unavailable,
            "NOT_NEEDED": self.not_needed,
            "REQUESTED_RESTRICTED": self.requested_restricted,
            "EXECUTION_ATTEMPTED": self.execution_attempted,
            "POLICY_VERSION": self.policy_version,
        }


def route_capabilities(
    text: str,
    project,
    capsule,
    available_tools: Iterable[str],
    *,
    available_adapters: Optional[Iterable[str]] = None,
    round_blocked: Optional[Iterable[str]] = None,
) -> CapabilityDecision:
    """Decide, per capability, ALLOW / BLOCK / NOT_NEEDED / UNAVAILABLE + reason.

    ``project`` may be the official hermes_cli.projects_db.Project or the internal
    NormalizedProjectState (duck-typed). ``capsule`` must already be bound via
    resolve_capsule() for this project — pass None when outside any project space.
    Nothing here executes a tool; it returns a decision only."""
    avail = set(available_tools or ())
    adapters = set((available_adapters or ()))
    policy = set(round_blocked or ())
    intent = infer_intent(text)
    prof = task_profile(text)
    restricted = detect_restricted(text)

    pid = _project_identity(project)[0] if project else None
    cid = getattr(capsule, "id", None) if capsule else None
    in_scope = bool(project and capsule and capsule.id == pid)

    capsule_blocked = set((capsule.data.get("blocked_capabilities") or []) if capsule else ())
    allowed_tools = set((capsule.data.get("allowed_tools") or []) if capsule else [])

    verdicts = {}
    for cap in CAP_ORDER:
        spec = CAPABILITIES[cap]
        tool = spec["tool"]
        # 1) Scope isolation first: PROJECT_CONTEXT needs a bound project scope.
        if spec.get("needs_project") and not in_scope:
            verdicts[cap] = CapabilityVerdict(cap, "NOT_NEEDED",
                                              "OUT_OF_PROJECT_SCOPE; project capsule not bound here", tool)
            continue
        # 2) Task relevance — unrelated capabilities stay NOT_NEEDED (nothing loaded).
        if cap not in prof:
            verdicts[cap] = CapabilityVerdict(cap, "NOT_NEEDED", "TASK_NOT_RELEVANT", tool)
            continue
        # 3) Precedence: capsule block > round policy > availability > tool grant.
        if cap in capsule_blocked:
            verdicts[cap] = CapabilityVerdict(cap, "BLOCK", "CAPSULE_BLOCKED (project boundary overrides model)", tool)
        elif cap in policy:
            verdicts[cap] = CapabilityVerdict(cap, "BLOCK", "ROUND_POLICY_BLOCKED", tool)
        elif tool not in avail:
            verdicts[cap] = CapabilityVerdict(cap, "UNAVAILABLE",
                                              f"TOOL_UNAVAILABLE ({tool} not in this runtime)", tool)
        elif allowed_tools and tool not in allowed_tools:
            verdicts[cap] = CapabilityVerdict(cap, "BLOCK", "TOOL_NOT_IN_CAPSULE_ALLOWED_TOOLS", tool)
        else:
            verdicts[cap] = CapabilityVerdict(cap, "ALLOW", "TASK_RELEVANT + CAPSULE_GRANTED + TOOL_AVAILABLE", tool)

    # Restricted / external requests: classified, never executed.
    requested = {}
    for r in sorted(restricted):
        # external_agent: UNAVAILABLE only when the adapter genuinely can't be located;
        # otherwise BLOCK (this round forbids external agents regardless of availability).
        if r == "external_agent":
            if "CX" in adapters or "H_NATIVE" in adapters:
                requested[r] = CapabilityVerdict(r, "BLOCK",
                                                 "ROUND_POLICY_PROHIBITED (external agent not executed this round)")
            else:
                requested[r] = CapabilityVerdict(r, "UNAVAILABLE",
                                                 "ADAPTER_NOT_LOCATED (no CX/H_NATIVE adapter in this runtime)")
        elif r == "write_delete":
            reason = "CAPSULE_BLOCKED" if "write_delete" in capsule_blocked or "delete" in capsule_blocked \
                else "ROUND_POLICY_PROHIBITED"
            requested[r] = CapabilityVerdict(r, "BLOCK", f"{reason} (file mutation not executed this round)")
        else:  # code_edit
            reason = "CAPSULE_BLOCKED" if "code_edit" in capsule_blocked else "ROUND_POLICY_PROHIBITED"
            requested[r] = CapabilityVerdict(r, "BLOCK", f"{reason} (source mutation not executed this round)")

    selected = [c for c, v in verdicts.items() if v.status == "ALLOW"]
    blocked = [c for c, v in verdicts.items() if v.status == "BLOCK"]
    unavailable = [c for c, v in verdicts.items() if v.status == "UNAVAILABLE"]
    not_needed = [c for c, v in verdicts.items() if v.status == "NOT_NEEDED"]

    return CapabilityDecision(
        task=text, intent=intent, project=pid, capsule=cid, in_scope=in_scope,
        verdicts={c: asdict(v) for c, v in verdicts.items()},
        selected=selected, blocked=blocked, unavailable=unavailable, not_needed=not_needed,
        requested_restricted={k: asdict(v) for k, v in requested.items()},
        execution_attempted=False,
    )


def main() -> int:
    import argparse
    from types import SimpleNamespace
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("text")
    p.add_argument("--project-id", type=str, default="",
                   help="Hermes project id; empty = outside any project space")
    p.add_argument("--capsule", type=Path, default=None, help="selftest capsule path")
    p.add_argument("--available-tools", type=str, default="",
                   help="comma-sep real tool names available in this runtime")
    p.add_argument("--adapters", type=str, default="",
                   help="comma-sep available adapter ids (H_NATIVE/CX)")
    a = p.parse_args()

    tools = [x for x in a.available_tools.split(",") if x]
    adapters = [x for x in a.adapters.split(",") if x]

    # Outside a project space (no --project-id): project stays None so the capsule
    # is NOT used for routing (scope isolation).
    project = None
    capsule = None
    if a.project_id:
        project = SimpleNamespace(id=a.project_id, project_id=a.project_id)
        if a.capsule and a.capsule.is_file():
            from .core import Project
            cap = Project.load(a.capsule)
            # In-scope only when the capsule actually binds to this project id.
            capsule = cap if cap.id == a.project_id else None

    decision = route_capabilities(a.text, project, capsule, tools, available_adapters=adapters)
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

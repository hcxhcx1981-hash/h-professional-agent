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
import hashlib
import re
import sqlite3

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


# ---- Runtime tool discovery (V0.1: read-only facts, no health probe) --------
# Discovery is SEPARATE from routing: it supplies facts about which tools are
# declared/requested in the current Hermes runtime, and the pure router above
# does all ALLOW/BLOCK decisions from those facts.
#
# Semantics (three tiers, only the first two are producible without probing):
#   REGISTERED        : Hermes has declared the tool globally (toolset catalog
#                      BUILTIN_TOOL_NAMES) or it is registered into the live
#                      registry (plugin/extension). Proof of declaration only.
#   SESSION_REQUESTED : the active session's toolset selection requests the tool
#                      (model_tools._select_tool_names, before check_fn). The
#                      most specific read-only session-level fact; NOT a health
#                      confirmation.
#   AVAILABLE         : confirmed healthy/callable — requires running a tool's
#                      check_fn (external probe). FORBIDDEN this round, so
#                      health_status is always "UNKNOWN" and never "AVAILABLE".

@dataclass(frozen=True)
class ToolDiscovery:
    """Read-only discovery facts. Never executes a tool, never probes health.

    `session_tools` is the set the active session's toolset selection requests
    (check_fn-free, official read-only source); `registered_tools` is the
    global declaration union. The session set is preferred; `degraded` marks
    the fallback to registration-only when the session source is unreachable.
    """
    session_tools: frozenset
    registered_tools: frozenset
    session_count: int
    registered_count: int
    degraded: bool
    health_status: str = "UNKNOWN"   # never 'AVAILABLE' without a probe
    probed: bool = False
    source: str = ""

    def mapped_tools(self) -> set:
        """Tool names of the six candidate capabilities."""
        return {CAPABILITIES[c]["tool"] for c in CAP_ORDER}

    def runtime_tools(self) -> list:
        """Facts to feed the pure router: session-requested tools relevant to
        our capabilities (preferred), else registered ones when degraded.
        'present' here means declared/requested — health NOT confirmed."""
        base = self.session_tools if not self.degraded else self.registered_tools
        return sorted(t for t in self.mapped_tools() if t in base)

    def missing_mapped(self) -> set:
        """Mapped tools absent from the preferred presence set."""
        base = self.session_tools if not self.degraded else self.registered_tools
        return {t for t in self.mapped_tools() if t not in base}

    def without(self, tool_names, *, session: bool = True) -> "ToolDiscovery":
        """Model a runtime that lacks the given tools, WITHOUT probing. The
        caller supplies the names (e.g. a test simulating an absent backend);
        we only drop them from the presence set(s)."""
        drop = frozenset(tool_names)
        if session:
            return ToolDiscovery(
                session_tools=self.session_tools - drop,
                registered_tools=self.registered_tools - drop,
                session_count=max(0, self.session_count - len(drop)),
                registered_count=max(0, self.registered_count - len(drop)),
                degraded=self.degraded, health_status=self.health_status,
                probed=self.probed, source=self.source + f"; simulated-exclusion={sorted(drop)}")
        return ToolDiscovery(
            session_tools=self.session_tools,
            registered_tools=self.registered_tools - drop,
            session_count=self.session_count,
            registered_count=max(0, self.registered_count - len(drop)),
            degraded=self.degraded, health_status=self.health_status,
            probed=self.probed, source=self.source + f"; simulated-exclusion(registered)={sorted(drop)}")


def _session_tool_names(enabled_toolsets, disabled_toolsets):
    """Active-session requested tool names via the official read-only resolver
    (model_tools._select_tool_names: toolset selection BEFORE check_fn)."""
    try:
        from model_tools import _select_tool_names
        sel = _select_tool_names(list(enabled_toolsets), list(disabled_toolsets),
                                 quiet_mode=True)
        return frozenset(sel) if sel is not None else None
    except Exception:
        return None


def _registered_tool_names() -> "tuple[frozenset, str]":
    """Global declaration union: toolset catalog names + live registry names."""
    parts = []
    names = set()
    try:
        from toolsets import BUILTIN_TOOL_NAMES
        names.update(BUILTIN_TOOL_NAMES)
        parts.append(f"catalog={len(BUILTIN_TOOL_NAMES)}")
    except Exception:
        parts.append("catalog=unreachable")
    try:
        from tools.registry import registry
        entries = registry.get_all_entries()
        names.update(e.name for e in entries)
        parts.append(f"registry={len(entries)}")
    except Exception:
        parts.append("registry=unreachable")
    return frozenset(names), ";".join(parts)


def discover_runtime_tools(
    enabled_toolsets=None,
    disabled_toolsets=None,
    *,
    injected_session=None,
    injected_registered=None,
) -> ToolDiscovery:
    """Read-only discovery of tools present in the current Hermes runtime.

    Production: session set from the active profile's toolset selection
    (``enabled_toolsets``/``disabled_toolsets`` read from config when not
    passed); registration union as the fallback base. Test: inject both sets
    so the adapter is unit-testable without a Hermes checkout. Either way
    NOTHING executes a tool and health is never probed.

    Prefers SESSION_REQUESTED; falls back to REGISTERED-only (degraded=True)
    when the official session source is unreachable, and says so in source —
    a registration-only result is never described as runtime-available tools.
    """
    if injected_session is None and injected_registered is None:
        if enabled_toolsets is None or disabled_toolsets is None:
            enabled_toolsets, disabled_toolsets = _load_profile_toolset_selection()
        session = _session_tool_names(enabled_toolsets, disabled_toolsets)
        if session is None:
            # Session source unreachable: degrade to registration discovery.
            reg, reg_source = _registered_tool_names()
            return ToolDiscovery(frozenset(), reg, 0, len(reg), True,
                                 "UNKNOWN", False,
                                 f"degraded=REGISTERED_DISCOVERY ({reg_source})")
        reg, reg_source = _registered_tool_names()
        return ToolDiscovery(
            session, reg, len(session), len(reg), False,
            "UNKNOWN", False,
            f"session=requested(pre-check_fn,enabled={sorted(enabled_toolsets)},disabled={sorted(disabled_toolsets)}); {reg_source}; health=UNKNOWN(no-probe)")
    reg = frozenset(injected_registered) if injected_registered is not None else frozenset()
    session = frozenset(injected_session) if injected_session is not None else frozenset()
    degraded = injected_session is None
    return ToolDiscovery(session, reg, len(session), len(reg), degraded,
                         "UNKNOWN", False,
                         "injected-facts (test); health=UNKNOWN(no-probe)")


def _load_profile_toolset_selection() -> "tuple[list, list]":
    """The active profile's toolset selection, read-only from Hermes config."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        enabled = [str(t) for t in (cfg.get("toolsets") or [])]
        disabled = [str(t) for t in (((cfg.get("agent") or {}).get("disabled_toolsets")) or [])]
        return enabled, disabled
    except Exception:
        return [], []


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
    runtime_tools: Iterable[str],
    *,
    available_adapters: Optional[Iterable[str]] = None,
    round_blocked: Optional[Iterable[str]] = None,
) -> CapabilityDecision:
    """Decide, per capability, ALLOW / BLOCK / NOT_NEEDED / UNAVAILABLE + reason.

    ``project`` may be the official hermes_cli.projects_db.Project or the internal
    NormalizedProjectState (duck-typed). ``capsule`` must already be bound via
    resolve_capsule() for this project — pass None when outside any project space.
    ``runtime_tools`` is the set of tool names PRESENT in this runtime/session,
    as discovered read-only (see discover_runtime_tools / ToolDiscovery). Presence
    here is registration/session-request, NOT a health confirmation — a capability
    can be ALLOWed on registration grounds without the tool being probe-verified.
    Nothing here executes a tool; it returns a decision only."""
    present = set(runtime_tools or ())
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
        elif tool not in present:
            verdicts[cap] = CapabilityVerdict(cap, "UNAVAILABLE",
                                              f"TOOL_NOT_REGISTERED ({tool} absent from this runtime/session)", tool)
        elif allowed_tools and tool not in allowed_tools:
            verdicts[cap] = CapabilityVerdict(cap, "BLOCK", "TOOL_NOT_IN_CAPSULE_ALLOWED_TOOLS", tool)
        else:
            verdicts[cap] = CapabilityVerdict(cap, "ALLOW",
                                              "TASK_RELEVANT + CAPSULE_GRANTED + TOOL_REGISTERED (health=UNKNOWN)", tool)

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


# Phase 2B extends this router with agent recommendations; it has no executor.
AGENT_TARGETS = {'SELF', 'CX', 'WB', 'AC'}
TASK_TYPES = {'STATUS', 'LOCAL_ANALYSIS', 'DOCUMENT_ANALYSIS', 'RESEARCH', 'ENGINEERING', 'CONFIGURATION', 'UNKNOWN'}


@dataclass(frozen=True)
class TaskProfile:
    task_type: str = 'UNKNOWN'
    requires_local_code_change: bool | None = None
    requires_web_research: bool | None = None
    requires_deep_engineering: bool | None = None
    requires_git_operations: bool | None = None
    requires_browser_or_gui: bool | None = None
    requires_sensitive_access: bool | None = None
    requires_high_context: bool | None = None
    requires_project_memory: bool | None = None
    estimated_complexity: str = 'UNKNOWN'
    risk_level: str = 'UNKNOWN'
    preferred_agent: str | None = None
    forbidden_agents: tuple[str, ...] = ()
    requested_skill: str | None = None


def build_task_profile(text, characteristics=None):
    """Conservative action/object extraction, then explicit structured facts.

    The agent policy below never matches words. Bare nouns are not actions.
    Missing facts stay None/UNKNOWN; this parser is intentionally bounded.
    """
    facts, evidence = {}, []
    patterns = {
        'requires_local_code_change': r'(?:修改|增加|实现|开发|重构)[^。；\n]{0,70}(?:Python实现|Python 实现|代码|功能|仓库|工程|h-professional-agent)|(?:跨文件重构|正式开发)|\b(?:implement|refactor|develop)\b.{0,50}\b(?:code|feature|repository)\b',
        'requires_web_research': r'(?:搜索|搜集|研究|检索)[^。；\n]{0,60}(?:资料|框架|来源|最近|报告|公开信息)|\b(?:research|search)\b.{0,50}\b(?:sources|framework|report|web)\b',
        'requires_deep_engineering': r'跨文件|正式(?:功能|开发|.*重构)|架构实现|\b(?:cross-file|architecture implementation)\b',
        'requires_git_operations': r'Git\s*收口|(?:仓库|重构)[^。；\n]{0,40}提交|\b(?:git commit|git push)\b',
        'requires_browser_or_gui': r'(?:操作|打开|控制)[^。；\n]{0,20}(?:浏览器|GUI|界面)|\b(?:operate|control)\b.{0,20}\b(?:browser|GUI)\b',
        'requires_sensitive_access': r'(?:读取|使用|获取|泄露)[^。；\n]{0,20}(?:密钥|凭据|token|cookie|密码|\.env)|\b(?:read|access)\b.{0,20}\b(?:credentials|secrets|tokens)\b',
        'requires_high_context': r'跨文件|架构实现|\bhigh context\b',
        'requires_project_memory': r'(?:读取|回忆|查询)[^。；\n]{0,25}(?:Memory|记忆|历史决策)',
    }
    for name, pattern in patterns.items():
        match = re.search(pattern,text,re.I)
        if match:
            # Negated operations cannot establish a positive task characteristic.
            prefix=text[max(0,match.start()-6):match.start()]
            if re.search(r'不要|禁止|不需要|无需|不能|do not|no ',prefix,re.I):
                facts[name]=False
            else:
                facts[name]=True
            evidence.append(dict(source='USER_REQUEST',field=name,signal=match.group()))
    status = bool(re.search(r'(?:检查|查询|读取|告诉我)[^。；\n]{0,50}(?:项目状态|Registry|下一步|Capsule|Memory|记忆)|\b(?:check|read)\b.{0,40}\b(?:project status|registry|capsule|memory)\b',text,re.I))
    document = bool(re.search(r'(?:分析|读取|总结)[^。；\n]{0,30}(?:PDF|文档|文件)|\b(?:analyze|read)\b.{0,30}\b(?:document|file|pdf)\b',text,re.I))
    if facts.get('requires_local_code_change'):
        facts.update(task_type='ENGINEERING',estimated_complexity='HIGH' if facts.get('requires_deep_engineering') else 'MEDIUM',risk_level='MEDIUM')
    elif facts.get('requires_web_research'):
        facts.update(task_type='RESEARCH',estimated_complexity='LOW',risk_level='LOW')
    elif status or document:
        facts.update(task_type='STATUS' if status else 'DOCUMENT_ANALYSIS',estimated_complexity='LOW',risk_level='LOW')
    elif re.search(r'(?:当前仓库|当前项目)[^。；\n]{0,30}(?:跑测试|运行测试|构建)|\b(?:run tests|build)\b.{0,30}\b(?:repository|project)\b',text,re.I):
        facts.update(task_type='ENGINEERING',estimated_complexity='MEDIUM',risk_level='MEDIUM')
    elif re.search(r'(?:调整|修改|检查)[^。；\n]{0,25}简单配置',text):
        facts.update(task_type='CONFIGURATION',estimated_complexity='LOW',risk_level='MEDIUM')
    if re.search(r'(?:用|使用)\s*MinerU',text,re.I): facts['requested_skill']='mineru'
    if re.search(r'删除|转账|生产部署|扩权|\b(?:delete|payment|production deployment)\b',text,re.I): facts['risk_level']='HIGH'
    if not text.strip(): facts={}
    if characteristics is not None:
        if not isinstance(characteristics,dict) or not set(characteristics)<=set(TaskProfile.__dataclass_fields__): raise ValueError('TASK_PROFILE_INVALID')
        for key,value in characteristics.items():
            if key in {'requires_sensitive_access','requires_browser_or_gui','requires_deep_engineering','requires_git_operations','requires_high_context'} and facts.get(key) is True and value is not True:
                raise ValueError('TASK_CHARACTERISTIC_CONFLICT')
            facts[key]=tuple(value) if key=='forbidden_agents' and isinstance(value,(list,tuple)) else value
            evidence.append(dict(source='STRUCTURED_TASK_CONTEXT',field=key,value=value))
    profile=TaskProfile(**facts)
    for key in patterns:
        if getattr(profile,key) is not None and type(getattr(profile,key)) is not bool: raise ValueError('TASK_PROFILE_INVALID')
    if profile.task_type not in TASK_TYPES or profile.estimated_complexity not in {'LOW','MEDIUM','HIGH','UNKNOWN'} or profile.risk_level not in {'LOW','MEDIUM','HIGH','UNKNOWN'}: raise ValueError('TASK_PROFILE_INVALID')
    if profile.preferred_agent is not None and profile.preferred_agent not in AGENT_TARGETS: raise ValueError('TASK_PROFILE_INVALID')
    if not isinstance(profile.forbidden_agents,tuple) or any(a not in AGENT_TARGETS for a in profile.forbidden_agents): raise ValueError('TASK_PROFILE_INVALID')
    if profile.requested_skill is not None and (not isinstance(profile.requested_skill,str) or not re.fullmatch(r'[a-z0-9_-]+',profile.requested_skill)): raise ValueError('TASK_PROFILE_INVALID')
    if re.search(r'删除|转账|生产部署|扩权|\b(?:delete|payment|production deployment)\b',text,re.I) and profile.risk_level!='HIGH': raise ValueError('UNSAFE_RISK_DOWNGRADE')
    return profile,evidence


@dataclass(frozen=True)
class TaskRoutingDecision:
    route_target: str
    decision: str
    confidence: float
    reasons: tuple
    evidence: tuple
    blocked_reasons: tuple
    requires_user_approval: bool
    fallback_target: str | None
    task_profile: TaskProfile
    recommended_sequence: tuple = ()
    handoff_package: dict | None = None
    execution_attempted: bool = False
    contract_version: str = 'task-routing.v0.1'


def route_task(text, registry, project_reference=None, *, characteristics=None,
               capsule=None, verified_skills=(), ac_suitability=None,
               acceptance_criteria=(), relevant_paths=(), source_requirements=()):
    """Registry-bound task recommendation + human-reviewed handoff; never dispatch."""
    from .continuation import ContinueRequest, ContinueResolution
    from .registry import workspace_key
    profile=TaskProfile()
    evidence=[]
    def blocked(reason, sequence=()):
        return TaskRoutingDecision('BLOCKED','NEEDS_DECOMPOSITION' if sequence else 'BLOCKED',0.0,
            (),tuple(evidence),(reason,),True,None,profile,tuple(sequence))
    supplied=json.dumps([text,characteristics,acceptance_criteria,relevant_paths,source_requirements,verified_skills,ac_suitability],default=str)
    if re.search(r'\bsk-[A-Za-z0-9_-]{16,}|(?:api[_ -]?key|token|cookie|password)\s*[=:]\s*["\']?[A-Za-z0-9_./+-]{12,}|Bearer\s+[A-Za-z0-9_./+-]{12,}',supplied,re.I):
        return blocked('CREDENTIAL_SHAPED_INPUT_NOT_EXPORTABLE')
    if not getattr(registry,'canonical',False): return blocked('CANONICAL_REGISTRY_REQUIRED')
    context=registry.continue_request(ContinueRequest(project_reference or text),capsule)
    if not isinstance(context,ContinueResolution) or context.status!='RESOLVED':
        return blocked(context.get('status','PROJECT_UNRESOLVED') if isinstance(context,dict) else context.status)
    record=next(p for p in registry.records if p.project_id==context.project_id)
    evidence.append(dict(source='CANONICAL_REGISTRY',project_id=record.project_id,
        machine_id=record.machine_id,workspace=context.workspace,h_project_id=record.h_project_id))
    objective=text.strip() or record.next_action or ''
    try: profile, signals=build_task_profile(objective,characteristics)
    except (ValueError,TypeError): return blocked('TASK_PROFILE_INVALID')
    evidence.extend(signals)
    if not text.strip(): evidence.append(dict(source='H_PROJECT_STATE',field='next_action'))
    if not objective: return blocked('OBJECTIVE_MISSING')
    if profile.risk_level in {'HIGH','UNKNOWN'} or profile.requires_sensitive_access is True:
        return blocked('RISK_OR_SENSITIVE_ACCESS_REQUIRES_REVIEW')
    if profile.requires_browser_or_gui is True: return blocked('BROWSER_GUI_SCOPE_UNVERIFIED')
    if profile.requires_high_context is True and capsule is None and not acceptance_criteria:
        return blocked('ENGINEERING_CONTEXT_OR_ACCEPTANCE_MISSING')
    if capsule is not None:
        if capsule.id!=record.project_id or workspace_key(str(capsule.root))!=workspace_key(context.workspace): return blocked('CAPSULE_BINDING_MISMATCH')
        restrictions=set(capsule.data.get('blocked_capabilities',()))
        if profile.requires_local_code_change and 'code_edit' in restrictions: return blocked('CAPSULE_BLOCKS_CODE_CHANGE')
        evidence.append(dict(source='BOUND_CAPSULE',ref=str(capsule.capsule),digest=capsule.digest))
    engineering=profile.task_type=='ENGINEERING' or profile.requires_local_code_change is True or profile.requires_deep_engineering is True or profile.requires_git_operations is True
    research=profile.requires_web_research is True
    if engineering and research: return blocked('RESEARCH_AND_ENGINEERING_REQUIRE_STAGES',('WB','CX'))
    skill=None
    for claim in verified_skills:
        if not isinstance(claim,dict): return blocked('SKILL_EVIDENCE_INVALID')
        if (claim.get('verified') is True and claim.get('low_risk') is True
            and claim.get('project_id')==record.project_id and claim.get('machine_id')==record.machine_id
            and claim.get('task_type')==profile.task_type and claim.get('evidence_ref')
            and (profile.requested_skill is None or claim.get('skill_id')==profile.requested_skill)):
            skill=claim; break
    if profile.requested_skill and skill is None: return blocked('REQUESTED_H_SKILL_NOT_VERIFIED')
    if skill and profile.risk_level=='LOW' and not profile.requires_deep_engineering and not profile.requires_git_operations:
        target,reason='SELF','VERIFIED_SCOPED_H_SKILL; AVOID_EXTERNAL_AGENT_COST'
        evidence.append(dict(source='VERIFIED_H_SKILL',ref=skill['evidence_ref'],skill_id=skill.get('skill_id')))
    elif engineering:
        target,reason='CX','FORMAL_ENGINEERING_OR_GIT; CX_PRIMARY_DEVELOPER'
        if profile.preferred_agent=='AC':
            claim=ac_suitability or {}
            suitable=(isinstance(claim,dict) and claim.get('verified') is True
                and claim.get('project_id')==record.project_id and claim.get('machine_id')==record.machine_id
                and claim.get('task_type')==profile.task_type and claim.get('evidence_ref')
                and profile.risk_level=='LOW' and profile.estimated_complexity=='LOW'
                and profile.requires_deep_engineering is False and profile.requires_git_operations is False)
            if not suitable: return blocked('AC_SUITABILITY_NOT_VERIFIED_OR_CX_REQUIRED')
            target,reason='AC','EXPLICIT_SCOPED_AC_SUITABILITY; LOW_RISK_BOUNDED_CHANGE'
            evidence.append(dict(source='AC_SUITABILITY',ref=claim['evidence_ref']))
    elif research or profile.task_type=='CONFIGURATION':
        if profile.estimated_complexity!='LOW': return blocked('ROUTINE_TASK_COMPLEXITY_UNCONFIRMED')
        target,reason='WB','ROUTINE_RESEARCH_OR_CONFIGURATION; CONSERVE_CX_QUOTA'
    elif profile.task_type in {'STATUS','LOCAL_ANALYSIS','DOCUMENT_ANALYSIS'}:
        target,reason='SELF','LOW_RISK_LOCAL_CONTEXT_ANALYSIS; NO_HANDOFF_NEEDED'
    else: return blocked('TASK_CHARACTERISTICS_INSUFFICIENT')
    if target in profile.forbidden_agents: return blocked('TARGET_FORBIDDEN_BY_USER')
    if target!='SELF' and capsule is not None and 'external_agent' in capsule.data.get('blocked_capabilities',()):
        return blocked('CAPSULE_BLOCKS_EXTERNAL_ROUTING')
    if profile.preferred_agent is not None and profile.preferred_agent!=target:
        return blocked('PREFERENCE_CONFLICTS_WITH_CAPABILITY_OR_COST_POLICY')
    handoff=None
    if target!='SELF':
        for values in (acceptance_criteria,relevant_paths,source_requirements):
            if not isinstance(values,(tuple,list)) or any(not isinstance(v,str) or not v.strip() for v in values): return blocked('HANDOFF_CONTEXT_INVALID')
        if not acceptance_criteria: return blocked('HANDOFF_ACCEPTANCE_CRITERIA_MISSING')
        if target in {'CX','AC'} and not relevant_paths: return blocked('MINIMAL_READ_SCOPE_MISSING')
        scoped=[]
        for path in relevant_paths:
            candidate=Path(path)
            candidate=(candidate if candidate.is_absolute() else Path(context.workspace)/candidate).resolve()
            if not candidate.is_relative_to(Path(context.workspace).resolve()): return blocked('HANDOFF_PATH_OUTSIDE_PROJECT')
            if candidate==Path(context.workspace).resolve(): return blocked('HANDOFF_READ_SCOPE_TOO_BROAD')
            if candidate.name.casefold().startswith('.env') or any(part.casefold() in {'vault','.git'} for part in candidate.parts): return blocked('SENSITIVE_OR_INTERNAL_PATH_FORBIDDEN')
            scoped.append(str(candidate))
        boundaries=['Single objective; human approval required; no automatic agent invocation',
            'No credentials or unrelated .env reads; no unrelated project access',
            'Minimal reads; no unbounded scans; no redundant verification; stop at acceptance']
        if target in {'CX','AC'}: boundaries.append('Follow $doraemon-local-dev-guard; preserve architecture unless required')
        else: boundaries.append('Research only; no unrelated development; cite primary sources and publication dates')
        handoff=dict(project_id=record.project_id,machine_id=record.machine_id,workspace=context.workspace,
            target_agent=target,objective=objective,context=dict(project_name=record.canonical_name,
            git_remote=record.repo_url,memory_namespace=record.memory_namespace,memory_ref=record.memory_ref),
            current_state=dict(status=record.status,next_action=record.next_action,last_active_at=record.last_activity),
            allowed_scope=scoped or ['Public source research for this objective only'],hard_boundaries=boundaries,
            acceptance_criteria=list(acceptance_criteria),relevant_paths=scoped,
            relevant_refs=[*context.source_refs,*([str(capsule.capsule)] if capsule else [])],
            source_requirements=list(source_requirements) or ['Primary sources with URLs and dates'],
            expected_return_format=['status','changes_or_findings','evidence','validation','remaining_gaps'],
            requires_user_approval=True,execution_allowed=False)
        digest=hashlib.sha256(json.dumps(handoff,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
        handoff['handoff_id']='handoff_'+digest
    return TaskRoutingDecision(target,'RECOMMEND',0.9 if characteristics else 0.75,(reason,),tuple(evidence),(),
        target!='SELF',None,profile,handoff_package=handoff)


def main() -> int:
    import argparse
    from types import SimpleNamespace
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("text")
    p.add_argument('--task-route',action='store_true',help='Phase 2B: recommendation + handoff only')
    p.add_argument('--hermes-home',type=Path)
    p.add_argument('--trusted-root',type=Path,action='append')
    p.add_argument('--registry-file',type=Path)
    p.add_argument('--acceptance',action='append',default=[])
    p.add_argument('--relevant-path',action='append',default=[])
    p.add_argument("--project-id", type=str, default="",
                   help="Hermes project id; empty = outside any project space")
    p.add_argument("--capsule", type=Path, default=None, help="selftest capsule path")
    p.add_argument("--available-tools", type=str, default="",
                   help="comma-sep tool names to treat as present (OVERRIDE; empty = auto-discover)")
    p.add_argument("--adapters", type=str, default="",
                   help="comma-sep available adapter ids (H_NATIVE/CX)")
    a = p.parse_args()
    if a.task_route:
        from .continuation import local_device,read_states
        from .registry import ProjectRegistry,DEFAULT_REGISTRY
        try:
            if not a.hermes_home or not a.trusted_root: raise ValueError('HOST_INPUTS_REQUIRED')
            device=local_device(a.hermes_home,a.trusted_root)
            view=ProjectRegistry.load(a.registry_file or DEFAULT_REGISTRY,device,read_states(a.hermes_home))
            capsule=None
            if a.capsule:
                from .core import Project
                capsule=Project.load(a.capsule)
            result=route_task(a.text,view,a.project_id or None,capsule=capsule,
                acceptance_criteria=a.acceptance,relevant_paths=a.relevant_path)
            print(json.dumps(asdict(result),ensure_ascii=False,indent=2))
            return 2 if result.route_target=='BLOCKED' else 0
        except (OSError,ValueError,sqlite3.Error):
            result=TaskRoutingDecision('BLOCKED','BLOCKED',0.0,(),(),('SOURCE_INVALID_OR_UNAVAILABLE',),True,None,TaskProfile())
            print(json.dumps(asdict(result)))
            return 2

    adapters = [x for x in a.adapters.split(",") if x]

    # Presence facts: explicit override, else read-only runtime discovery.
    if a.available_tools:
        tools = [x for x in a.available_tools.split(",") if x]
        tool_note = "manual-override"
    else:
        disc = discover_runtime_tools()
        tools = disc.runtime_tools()
        tool_note = disc.source

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
    out = decision.to_dict()
    out["TOOLS_SOURCE"] = tool_note
    out["HEALTH_STATUS"] = "UNKNOWN"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

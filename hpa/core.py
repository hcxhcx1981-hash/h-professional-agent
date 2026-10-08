from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PIN = "6492d704f9d389233038ee35630940acba7c2f2f"
MEMORY_PIN = "2a75f1dc56053e07516318aea69716f0333810f5"
# V0.1 required capsule fields. `FIELDS` is kept as an alias so existing imports/
# references keep working.
REQUIRED_FIELDS = {"version", "project_id", "project_name", "project_root", "allowed_skills", "allowed_tools", "memory_namespace", "artifact_workspace", "created_at"}
# Optional V0.1 annotation fields: express project purpose / current phase / blocked
# capabilities / provenance on the SAME capsule without a second capsule system.
# Existing 9-field capsules remain valid (strict subset of REQUIRED ∪ OPTIONAL).
OPTIONAL_ANNOTATION_FIELDS = {"project_purpose", "phase", "blocked_capabilities", "provenance"}
FIELDS = REQUIRED_FIELDS


class Blocked(ValueError):
    pass


def portable_path(value: str, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise Blocked("PATH_REQUIRED")
    # Literal absolute machine paths are forbidden in portable configuration.
    if Path(value).is_absolute() or re.match(r"^[A-Za-z]:", value) or value.startswith("\\\\"):
        raise Blocked("USE_ENV_OR_RELATIVE_PATH")
    expanded = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m[1], m[0]), value)
    if "${" in expanded:
        raise Blocked("ENV_PATH_UNRESOLVED")
    path = Path(expanded)
    return (path if path.is_absolute() else base / path).resolve()


def beneath(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise Blocked("PATH_OUTSIDE_PROJECT")
    return resolved


@dataclass(frozen=True)
class Project:
    data: dict
    capsule: Path
    root: Path
    artifacts: Path
    digest: str

    @property
    def id(self):
        return self.data["project_id"]

    @classmethod
    def load(cls, file: Path):
        file = file.resolve()
        raw = file.read_bytes()
        data = json.loads(raw)
        allowed_keys = REQUIRED_FIELDS | OPTIONAL_ANNOTATION_FIELDS
        if not set(data) <= allowed_keys or not set(data) >= REQUIRED_FIELDS or data["version"] != "0.1":
            raise Blocked("CAPSULE_SCHEMA_INVALID")
        for key in FIELDS - {"allowed_skills", "allowed_tools"}:
            if not isinstance(data[key], str) or not data[key].strip():
                raise Blocked("CAPSULE_FIELD_INVALID")
        for key in ("allowed_skills", "allowed_tools"):
            items = data[key]
            if not isinstance(items, list) or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in items) or len(items) != len(set(items)):
                raise Blocked("CAPABILITY_LIST_INVALID")
        if not data["allowed_skills"] or not re.fullmatch(r"[A-Za-z0-9_-]+", data["project_id"]):
            raise Blocked("PROJECT_ID_REQUIRED")
        try:
            if datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except ValueError as exc:
            raise Blocked("TIMESTAMP_INVALID") from exc
        root = portable_path(data["project_root"], file.parent)
        if not root.is_dir():
            raise Blocked("PROJECT_ROOT_MISSING")
        artifacts = beneath(portable_path(data["artifact_workspace"], root), root)
        if artifacts == root:
            raise Blocked("DEDICATED_ARTIFACT_WORKSPACE_REQUIRED")
        return cls(data, file, root, artifacts, hashlib.sha256(raw).hexdigest())

    def binding(self, conversation: str):
        if not conversation or not re.fullmatch(r"[A-Za-z0-9_-]+", conversation):
            raise Blocked("CONVERSATION_ID_REQUIRED")
        return dict(conversation=conversation, project_id=self.id, capsule_digest=self.digest, root=str(self.root))

    def validate_binding(self, binding: dict | None):
        fresh = Project.load(self.capsule)
        if not binding:
            raise Blocked("NO_PROJECT_BINDING")
        if binding != fresh.binding(binding.get("conversation", "")) or fresh.digest != self.digest:
            raise Blocked("STALE_OR_WRONG_BINDING")

    def write_artifact(self, binding, name: str, text: str):
        self.validate_binding(binding)
        if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name or ":" in name:
            raise Blocked("ARTIFACT_PATH_INVALID")
        target = beneath(self.artifacts / name, self.artifacts)
        beneath(target, self.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Own writes refuse existing links/files. No race-proof OS sandbox claim.
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return target


def select(project: Project, binding, task: str) -> dict:
    project.validate_binding(binding)
    verify_capsule_dependency()
    from capsule.router import route
    from capsule.schema import RunContext, SourceRecord
    records, tool_map = [], {}
    for name in project.data["allowed_skills"]:
        skill_dir = beneath(project.root / "skills" / name, project.root)
        source = beneath(skill_dir / "SKILL.md", project.root)
        metadata = json.loads(beneath(skill_dir / "capability.json", project.root).read_text(encoding="utf-8"))
        if not source.is_file() or metadata.get("name") != name:
            raise Blocked("SKILL_SOURCE_INVALID")
        tools = metadata.get("tools", [])
        if not isinstance(tools, list) or not set(tools) <= set(project.data["allowed_tools"]):
            raise Blocked("SKILL_TOOL_GRANT_EXCEEDS_CAPSULE")
        tool_map[name] = sorted(set(tools))
        records.append(SourceRecord("skill", str(skill_dir), name, "project", metadata["description"], trigger_phrases=metadata["triggers"], confidence=1.0))
    decision = route(RunContext(records=records), task, shortlist_size=4, min_score=3.0)
    scored = sorted((c.score for c in decision.considered if c.body_read), reverse=True)
    if not decision.confident or not decision.selected:
        raise Blocked("ROUTER_LOW_CONFIDENCE_CLARIFY")
    if len(scored) > 1 and scored[0] - scored[1] < 1.0:
        raise Blocked("ROUTER_AMBIGUOUS_CLARIFY")
    chosen = decision.selected.name
    if chosen not in project.data["allowed_skills"]:
        raise Blocked("ROUTER_OUTSIDE_PROJECT")
    return dict(project_id=project.id, skills=[chosen], tools=tool_map[chosen], rationale=decision.rationale, router_pin=PIN)


def verify_capsule_dependency():
    import capsule
    installed = Path(capsule.__file__).resolve().parent
    expected = json.loads(Path(__file__).with_name("capsule-integrity.json").read_text(encoding="utf-8"))
    if expected["commit"] != PIN:
        raise Blocked("ROUTER_PIN_MISMATCH")
    # Windows Git checkouts may use CRLF. Check code identity, not the host's
    # checkout newline conversion; every other byte still participates.
    actual = {str(file.relative_to(installed)).replace("\\", "/"): hashlib.sha256(file.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for file in installed.rglob("*.py")}
    if actual != expected["files"]:
        raise Blocked("ROUTER_DEPENDENCY_DRIFT")


def verify_repo(root: Path, expected: str):
    actual = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if actual != expected:
        raise Blocked("COMPONENT_HEAD_MISMATCH")
    if subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, check=True).stdout.strip():
        raise Blocked("COMPONENT_TRACKED_FILES_DIRTY")


def memory_context(project: Project, binding, task: str, memory_root: Path, python: Path, store: Path, budget=500):
    project.validate_binding(binding)
    # V0.1 deliberately allows only an explicitly supplied project fixture store.
    store = beneath(store, project.root)
    if store.suffix != ".json" or not store.name.startswith("synthetic-"):
        raise Blocked("SYNTHETIC_STORE_REQUIRED_V01")
    verify_repo(memory_root, MEMORY_PIN)
    command = [str(python), "-B", "-m", "cli", "--read-only", "--store", str(store), "smart-retrieve", task, "--project", project.data["memory_namespace"], "--agent", "hermes", "--machine", "synthetic-win10"]
    proc = subprocess.run(command, cwd=memory_root, capture_output=True, encoding="utf-8", check=True, timeout=30)
    found = json.loads(proc.stdout)
    lines, ids = [], []
    for row in found["results"]:
        record = row["record"]
        # Core includes global rows. The adapter uses exact namespace, never global fallback.
        if record["project"] != project.data["memory_namespace"]:
            continue
        line = f"[{record['id']}] {record['summary']}"
        if len("\n".join(lines + [line])) <= budget:
            lines.append(line)
            ids.append(record["id"])
    return dict(context="\n".join(lines), memory_ids=ids, namespace=project.data["memory_namespace"])


def shadow(project: Project, binding, dhaf_root: Path):
    project.validate_binding(binding)
    file = dhaf_root / "core" / "decision-gate" / "decision_gate.py"
    spec = importlib.util.spec_from_file_location("hpa_dhaf_shadow", file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    state = module.DecisionState(goal=module.GoalBaseline(["scoped result"]), progress=module.ProgressGate(True))
    path = beneath(project.artifacts / f"{binding['conversation']}-shadow.jsonl", project.root)
    row = module.record_shadow(path, binding["conversation"], state, "CONTINUE", "scoped_v01", True)
    if not row.get("recorded"):
        raise Blocked("DHAF_SHADOW_RECORD_FAILED")
    return dict(mode="SHADOW_ONLY", live_ready=False, recorded=True, recommendation_executed=False)

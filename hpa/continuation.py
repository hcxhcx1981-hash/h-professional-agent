"""Read -> normalize -> resolve -> recommend. No Agent invocation or state writes."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile

from .core import Project, memory_context

VERSION = 'personal-control-plane.v0.1'
SHANGHAI = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class DeviceContext:
    device_id: str
    hostname: str
    os: str
    user: str
    trusted_workspace_roots: tuple[str, ...]
    available_adapters: tuple[str, ...]
    last_seen: str
    source: str
    confidence: float
    trust_status: str = 'TRUSTED'


def local_device(hermes_home: Path, roots: list[Path]) -> DeviceContext:
    """CLI roots are explicit trusted host inputs, never extracted from a query."""
    host, user = socket.gethostname(), getpass.getuser()
    identity = (hermes_home / 'install_id').read_text(encoding='utf-8').strip()
    valid = bool(re.fullmatch(r'[0-9a-f]{32}', identity)) and platform.system() == 'Windows'
    build = platform.version().split('.')
    valid = valid and len(build) == 3 and build[-1].isdigit() and int(build[-1]) < 22000
    resolved = tuple(str(p.resolve(strict=True)) for p in roots if p.is_dir())
    # File existence establishes a recommendation candidate, NOT execution health/grant.
    adapters = []
    if (hermes_home / 'hermes-agent' / 'tools' / 'registry.py').is_file():
        adapters.append('H_NATIVE')
    if shutil.which('codex'):
        adapters.append('CX')
    device_id = hashlib.sha256(f'{identity}|{host}|{user}'.encode()).hexdigest()[:32]
    return DeviceContext(device_id, host, platform.platform(), user, resolved,
                         tuple(adapters), datetime.now(timezone.utc).isoformat(),
                         'local-host + existing H install identity + explicit CLI roots',
                         1.0 if valid else 0.0, 'TRUSTED' if valid else 'UNTRUSTED')


@contextmanager
def readonly_db(path: Path):
    # SQLite mode=ro may still update the source SHM. Use a disposable snapshot,
    # including WAL (immutable=1 alone would risk ignoring committed WAL state).
    # No persistent project store, migration, H imports or source SQLite handles.
    path = path.resolve(strict=True)
    sources = [path, Path(str(path) + '-wal')]
    with tempfile.TemporaryDirectory(prefix='hpa-read-snapshot-') as scratch:
        before = {p: hashlib.sha256(p.read_bytes()).digest() for p in sources if p.is_file()}
        target = Path(scratch) / 'snapshot.db'
        shutil.copyfile(path, target)
        if sources[1] in before:
            shutil.copyfile(sources[1], Path(str(target) + '-wal'))
        after = {p: hashlib.sha256(p.read_bytes()).digest() for p in sources if p.is_file()}
        copies = {p: hashlib.sha256((target if p == path else Path(str(target)+'-wal')).read_bytes()).digest()
                  for p in before}
        if before != after or copies != before:
            raise ValueError('SOURCE_CHANGED_DURING_SNAPSHOT')
        conn = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN')
            yield conn
        finally:
            conn.close()


@dataclass(frozen=True)
class NormalizedProjectState:
    project_id: str
    project_name: str
    workspace: str | None
    device_id: str | None
    status: str
    last_task: dict | None
    last_result: dict | None
    last_valid_state: dict | None
    next_action: str | None
    updated_at: int | None
    source_refs: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    activity_at: tuple[int, ...] = ()


def read_states(home: Path) -> list[NormalizedProjectState]:
    with readonly_db(home / 'projects.db') as c:
        projects = c.execute('SELECT id, name, slug, primary_path, board_slug '
                             'FROM projects WHERE archived=0').fetchall()
        folders = c.execute('SELECT project_id,path,is_primary FROM project_folders').fetchall()
    states = []
    for p in projects:
        workspace = p['primary_path'] or next((f['path'] for f in folders
                       if f['project_id'] == p['id'] and f['is_primary']), None)
        slug = p['board_slug'] or 'default'
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', slug):
            raise ValueError('UNSUPPORTED_BOARD_REFERENCE')
        board = home / 'kanban.db' if slug == 'default' else home / 'kanban' / 'boards' / slug / 'kanban.db'
        tasks = []
        event_times = []
        refs = [f'H/projects/{p["id"]}']
        if board.is_file():
            with readonly_db(board) as c:
                # Never read raw prompts, errors, credential-bearing outputs or chat logs.
                tasks = c.execute('SELECT id,status,created_at,started_at,completed_at,'
                                  'CASE WHEN result IS NULL THEN 0 ELSE 1 END AS has_result '
                                  'FROM tasks WHERE project_id=?', (p['id'],)).fetchall()
                if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_events'").fetchone():
                    event_times = [r[0] for r in c.execute('SELECT e.created_at FROM task_events e '
                        'JOIN tasks t ON t.id=e.task_id WHERE t.project_id=?', (p['id'],))]
            refs.append(f'H/kanban/{slug}')
        def stamp(t):
            return max(t['created_at'] or 0, t['started_at'] or 0, t['completed_at'] or 0)
        tasks = sorted(tasks, key=stamp, reverse=True)
        latest = tasks[0] if tasks else None
        valid = next((t for t in tasks if t['status'] == 'done' and t['has_result']), None)
        last_task = dict(id=latest['id'], status=latest['status']) if latest else None
        result = dict(task_id=valid['id'], source='H/Kanban', verification='HOST_RECORDED_NOT_EFFECT_VERIFIED') if valid else None
        status_map = {'todo': 'READY', 'in_progress': 'RUNNING', 'blocked': 'BLOCKED',
                      'done': 'COMPLETED', 'failed': 'FAILED', 'review': 'WAITING_APPROVAL'}
        states.append(NormalizedProjectState(p['id'], p['name'], workspace, None,
            status_map.get(latest['status'], 'UNKNOWN') if latest else 'IDLE',
            last_task, result, dict(status='COMPLETED', result_ref=result) if valid else None,
            '查询并整理项目状态', stamp(latest) if latest else None, tuple(refs),
            (p['slug'],), tuple(t[k] for t in tasks for k in ('created_at','started_at','completed_at')
                               if t[k] is not None) + tuple(event_times)))
    return states


@dataclass(frozen=True)
class ContinueRequest:
    text: str
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    intent: str = 'status'


@dataclass(frozen=True)
class RoutingRecommendation:
    adapter: str
    reason: str
    execution_allowed: bool = False
    availability: str = 'NOT_PROBED_FOR_EXECUTION'


def recommend(intent: str, device: DeviceContext, capsule: Project | None,
              project: NormalizedProjectState) -> RoutingRecommendation:
    if capsule is None or capsule.id != project.project_id:
        return RoutingRecommendation('NO_VALID_AGENT', 'CAPSULE_BINDING_MISSING_OR_MISMATCH')
    if capsule.root != Path(project.workspace).resolve():
        return RoutingRecommendation('NO_VALID_AGENT', 'CAPSULE_WORKSPACE_MISMATCH')
    target = {'status': 'H_NATIVE', 'query': 'H_NATIVE', 'memory': 'H_NATIVE',
              'analysis': 'H_NATIVE', 'develop': 'CX', 'test': 'CX', 'git': 'CX'}.get(intent)
    if not target or target not in device.available_adapters:
        return RoutingRecommendation('NO_VALID_AGENT', 'UNKNOWN_CAPABILITY_OR_ADAPTER_NOT_LOCATED')
    required = {'status': 'read_file', 'query': 'read_file', 'analysis': 'read_file',
                'memory': 'terminal', 'develop': 'terminal', 'test': 'terminal', 'git': 'terminal'}[intent]
    if required not in capsule.data['allowed_tools']:
        return RoutingRecommendation('NO_VALID_AGENT', 'REQUIRED_TOOL_NOT_ALLOWED_BY_CAPSULE')
    # Recommendation of engineering work never implies gate readiness or approval.
    return RoutingRecommendation(target, 'READ_ONLY_RECOMMENDATION; EXECUTION/GATE/HEALTH_NOT_VALIDATED')


def _project_identity(project) -> "tuple[str | None, str | None]":
    """Duck-type a Hermes project into (project_id, workspace) so the resolver works
    for both the internal NormalizedProjectState and the official hermes_cli
    projects_db.Project (which exposes .id and .primary_path/.folders, not .workspace)."""
    pid = getattr(project, "project_id", None) or getattr(project, "id", None)
    ws = getattr(project, "workspace", None)
    if ws is None:
        ws = getattr(project, "primary_path", None)
        if ws is None:
            folders = getattr(project, "folders", None)
            if folders:
                for f in folders:
                    if getattr(f, "is_primary", False):
                        ws = f.path
                        break
                else:
                    ws = folders[0].path
    return pid, ws


def resolve_capsule(project, candidate_paths) -> "Project | None":
    """Pick the Project Capsule whose identity AND workspace bind to this Hermes project.

    Reuses hpa.core.Project.load and the exact (project_id, root) criteria that
    recommend() already enforces; no second capsule system, no capsule/Memory OS/DHAF
    dependency. Returns None when no candidate binds (isolation: nothing leaks to a
    project that is not this one)."""
    from .core import Project
    pid, ws = _project_identity(project)
    if not pid or not ws:
        return None
    try:
        target_ws = Path(ws).resolve()
    except (OSError, TypeError):
        return None
    for cand in list(candidate_paths):
        try:
            cap = Project.load(cand)
        except Exception:
            continue
        if cap.id == pid and cap.root == target_ws:
            return cap
    return None


@dataclass(frozen=True)
class ContinueResolution:
    status: str
    project_id: str | None
    project_name: str | None
    workspace: str | None
    device_id: str
    confidence: float
    last_valid_state: dict | None
    last_task: dict | None
    last_result: dict | None
    next_candidate_action: str | None
    recommended_adapter: str
    reason: str
    ambiguity_candidates: tuple[dict, ...]
    source_refs: tuple[str, ...]
    device_context: DeviceContext
    routing: RoutingRecommendation | None = None
    memory_background: dict | None = None
    contract_version: str = VERSION


def resolve(request: ContinueRequest, device: DeviceContext,
            states: list[NormalizedProjectState], capsule: Project | None = None) -> ContinueResolution:
    def answer(status, reason, candidates=(), p=None, routing=None):
        return ContinueResolution(status, p.project_id if p else None,
            p.project_name if p else None, p.workspace if p and status == 'RESOLVED' else None,
            device.device_id, 1.0 if status == 'RESOLVED' else 0.0,
            p.last_valid_state if p else None, p.last_task if p else None,
            p.last_result if p else None, p.next_action if p else None,
            routing.adapter if routing else 'NO_VALID_AGENT', reason, tuple(candidates),
            p.source_refs if p else (), device, routing)
    try:
        fresh = datetime.fromisoformat(device.last_seen)
        age = (request.now - fresh).total_seconds()
        if device.trust_status != 'TRUSTED' or not 0 <= age <= 60:
            return answer('DEVICE_UNTRUSTED', 'DEVICE_IDENTITY_OR_OBSERVATION_EXPIRED')
    except (ValueError, TypeError):
        return answer('DEVICE_UNTRUSTED', 'INVALID_DEVICE_OBSERVATION')
    text = request.text.casefold()
    def named(name):
        if not name:
            return False
        escaped = re.escape(name.casefold())
        pattern = rf'(?<![a-z0-9_-]){escaped}(?![a-z0-9_-])' if name.isascii() else escaped
        return bool(re.search(pattern, text))
    matches = [p for p in states if any(named(name)
               for name in (p.project_name, *p.aliases))]
    generic_yesterday = re.sub(r'\s+', '', text) in {
        '继续昨天那个项目', '继续昨天的项目', '昨天那个项目', '昨天cx做到哪了'}
    if not matches and generic_yesterday:
        yesterday = (request.now.astimezone(SHANGHAI) - timedelta(days=1)).date()
        matches = [p for p in states if any(datetime.fromtimestamp(t, SHANGHAI).date() == yesterday
                                            for t in p.activity_at)]
    if not matches:
        return answer('NOT_FOUND', 'NO_REGISTERED_H_PROJECT_MATCH')
    if len(matches) != 1:
        return answer('AMBIGUOUS', '请选择哪个已登记项目；不会按最近活动猜测',
                      [dict(project_id=p.project_id, project_name=p.project_name) for p in matches])
    p = matches[0]
    if p.device_id and p.device_id != device.device_id:
        return answer('DEVICE_UNTRUSTED', 'PROJECT_DEVICE_MISMATCH', p=p)
    try:
        workspace = Path(p.workspace).resolve(strict=True)
        roots = [Path(r).resolve(strict=True) for r in device.trusted_workspace_roots]
        if not workspace.is_dir() or not any(workspace.is_relative_to(r) for r in roots):
            return answer('DEVICE_UNTRUSTED', 'WORKSPACE_OUTSIDE_TRUSTED_ROOTS', p=p)
    except (OSError, TypeError):
        return answer('DEVICE_UNTRUSTED', 'WORKSPACE_UNAVAILABLE_ON_CURRENT_DEVICE', p=p)
    route = recommend(request.intent, device, capsule, p)
    return answer('RESOLVED', 'UNIQUE_H_NAME_OR_ALIAS_OR_YESTERDAY_ACTIVITY; STATE_FROM_H', p=p, routing=route)


def with_memory(result: ContinueResolution, project: Project, root: Path,
                python: Path, task: str) -> ContinueResolution:
    """Reuse existing exact-namespace, pinned, synthetic-only read adapter."""
    if result.status != 'RESOLVED' or project.id != result.project_id:
        raise ValueError('MEMORY_REQUIRES_RESOLVED_PROJECT_BINDING')
    background = memory_context(project, project.binding('readonly-continuation'), task,
                                root, python, project.root / 'synthetic-memory.json')
    return replace(result, memory_background={**background, 'authority': 'BACKGROUND_ONLY'})


def infer_intent(text: str) -> str:
    if re.search(r'交易|转账|删除|外发|发送|扩权', text):
        return 'unknown'
    if re.search(r'代码修改|修改代码|开发|修复|repo.*修改|仓库.*修改', text, re.I):
        return 'develop'
    if re.search(r'测试|\bbuild\b', text, re.I):
        return 'test'
    if re.search(r'\bgit\b', text, re.I):
        return 'git'
    if re.search(r'继续|接着|查询|整理|状态|做到哪|分析', text):
        return 'status'
    return 'unknown'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('text')
    parser.add_argument('--hermes-home', type=Path, required=True)
    parser.add_argument('--trusted-root', type=Path, action='append', required=True)
    parser.add_argument('--capsule', type=Path)
    parser.add_argument('--memory-root', type=Path)
    parser.add_argument('--memory-python', type=Path)
    parser.add_argument('--intent', choices=['status', 'query', 'memory', 'analysis', 'develop', 'test', 'git', 'unknown'])
    args = parser.parse_args()
    try:
        device = local_device(args.hermes_home, args.trusted_root)
        states = read_states(args.hermes_home)
        capsule = Project.load(args.capsule) if args.capsule else None
        intent = args.intent or infer_intent(args.text)
        result = resolve(ContinueRequest(args.text, intent=intent), device, states, capsule)
        if args.memory_root or args.memory_python:
            if not args.memory_root or not args.memory_python or capsule is None:
                raise ValueError('EXPLICIT_MEMORY_ADAPTER_INPUTS_REQUIRED')
            if result.status == 'RESOLVED':
                result = with_memory(result, capsule, args.memory_root, args.memory_python, args.text)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        print(json.dumps({'status': 'DEVICE_UNTRUSTED', 'reason': 'SOURCE_UNAVAILABLE_OR_INVALID; NO_WRITES_PERFORMED'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

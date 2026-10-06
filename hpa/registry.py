"""Ephemeral project view. Source authorities stay in H, GitHub and Memory OS."""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from .continuation import (ContinueRequest, ContinueResolution, NormalizedProjectState,
    RoutingRecommendation, local_device, read_states, resolve)
from dataclasses import replace
import ntpath

DEFAULT_REGISTRY = Path(__file__).with_name('project-registry.local.json')
EXAMPLE_REGISTRY = Path(__file__).with_name('project-registry.example.json')

def workspace_key(value):
    return ntpath.normcase(ntpath.normpath(value))

MACHINES = {'WIN10_ADMIN', 'WIN11_LENOVO', 'MAC_WIN10'}
STATUSES = {'ACTIVE', 'PAUSED', 'WAITING_FOR_DEVICE', 'WAITING_FOR_INPUT', 'DONE', 'ARCHIVED', 'UNKNOWN'}

def repo_key(url):
    # Never echo URLs containing credentials or arbitrary hosts.
    match = re.fullmatch(r'(?:https://github.com/|git@github.com:)([\w.-]+)/([\w.-]+?)(?:\.git)?/?', url or '')
    return 'https://github.com/' + '/'.join(match.groups()).casefold() if match else None

def identity(prefix, value):
    return prefix + ':' + hashlib.sha256(value.encode()).hexdigest()[:20]

def command(args, **kwargs):
    return subprocess.run(args, capture_output=True, encoding='utf-8', timeout=30,
                          check=True, **kwargs).stdout

@dataclass
class ProjectRecord:
    project_id: str
    canonical_name: str
    aliases: list = field(default_factory=list)
    repo_url: str | None = None
    repo_visibility: str = 'UNKNOWN'
    local_workspaces: list = field(default_factory=list)
    machines: list = field(default_factory=list)
    current_machine_available: bool = False
    status: str = 'UNKNOWN'
    last_activity: object = None
    last_valid_state: object = None
    next_action: str | None = None
    github_state: object = 'UNKNOWN'
    h_project_state: object = None
    memory_state: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    confidence: float = 0.0
    conflicts: list = field(default_factory=list)
    updated_at: str = ''
    machine_id: str | None = None
    workspace: str | None = None
    h_project_id: str | None = None
    project_type: str = 'UNKNOWN'
    memory_namespace: str | None = None
    memory_ref: str | None = None
    tags: list = field(default_factory=list)
    availability: str = 'UNAVAILABLE'


def github_source(owner, gh):
    if not re.fullmatch(r'[A-Za-z0-9-]+', owner):
        raise ValueError('INVALID_GITHUB_OWNER')
    try:
        # One read request; no login, authorization, retry or mutation.
        rows = json.loads(command([str(gh), 'repo', 'list', owner, '--limit', '1000',
            '--json', 'name,url,visibility,defaultBranchRef,updatedAt,isArchived']))
        return [dict(source='GITHUB', name=r['name'], repo_url=repo_key(r['url']),
            github=dict(visibility=r['visibility'], default_branch=(r['defaultBranchRef'] or {}).get('name'),
                        updated_at=r['updatedAt'], archived=r['isArchived'])) for r in rows], (
                        'COMPLETE' if len(rows) < 1000 else 'TRUNCATED')
    except (OSError, ValueError, subprocess.SubprocessError):
        return [], 'GITHUB_UNAVAILABLE'


def local_source(roots, explicit=()):
    # Exactly one directory level, maximum 200 children per root; no recursion.
    candidates = set()
    for root in roots:
        root = root.resolve(strict=True)
        candidates.add(root)
        children = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p:p.name)[:200]
        candidates.update(children)
    for path in explicit:
        p = Path(path)
        if p.is_dir() and any(p.resolve().is_relative_to(r.resolve()) for r in roots):
            candidates.add(p)
    rows = []
    for p in sorted(candidates):
        if p.is_symlink() or not p.is_dir() or not any(p.resolve().is_relative_to(r.resolve()) for r in roots):
            continue
        git = (p / '.git').exists()
        metadata = any((p / f).is_file() for f in ('pyproject.toml','package.json','Cargo.toml'))
        readme = any((p / f).is_file() for f in ('README.md','README','readme.md'))
        if not git and not (metadata and readme):
            continue
        url = None
        if git:
            try:
                url = repo_key(command(['git','-C',str(p), 'config','--get','remote.origin.url']).strip())
            except (OSError, subprocess.SubprocessError):
                pass
        rows.append(dict(source='LOCAL', name=p.name, workspace=str(p.resolve()),
                         machine='WIN10_ADMIN', repo_url=url, evidence='git' if git else 'manifest+README'))
    return rows


def h_source(home):
    return [dict(source='H', name=p.project_name, project_id=p.project_id,
        workspace=p.workspace, machine='WIN10_ADMIN', aliases=list(p.aliases), state=asdict(p))
        for p in read_states(home)]


def memory_source(root, python, stores):
    # Existing Memory OS APIs own lifecycle/retrieval. Only structured metadata is
    # projected; prose cannot create aliases, identity, workspace or machine claims.
    script = r"""
import json, sys
from core.engine import Memory, sensitive
out=[]
for path in sys.argv[1:]:
    m=Memory(path,read_only=True)
    namespaces={r.get('project') for r in m.load()['records'] if r.get('project')}
    for ns in sorted(namespaces):
        for r in m.search('',project=ns,agent='hermes',limit=1000):
            if r['type'] not in ('PROJECT','DECISION','WORKFLOW'): continue
            if str(r.get('source_type','')).lower() in ('synthetic','fixture','test') or str(ns).lower().startswith('synthetic'): continue
            meta=r.get('metadata',{}).get('project_registry',{})
            if not isinstance(meta,dict) or not any(meta.get(k) for k in ('canonical_id','repo_url','workspace')): continue
            # Namespace alone is a candidate, never proof of a physical workspace.
            safe={k:meta[k] for k in ('canonical_id','canonical_name','repo_url','aliases','workspace','status','next_action','not_on_github') if k in meta}
            row=dict(source='MEMORY',name=safe.pop('canonical_name',ns),machine=r.get('machine'),memory_id=r['id'],updated_at=r['updated_at'],metadata=safe)
            if not sensitive(json.dumps(row,ensure_ascii=False)): out.append(row)
print(json.dumps(out,ensure_ascii=False))
"""
    try:
        return json.loads(command([str(python), '-X','utf8','-B','-c',script,*map(str,stores)], cwd=root)), 'READ_ONLY'
    except (OSError, ValueError, subprocess.SubprocessError):
        return [], 'MEMORY_UNAVAILABLE'


class ProjectRegistry:
    @classmethod
    def load(cls, path, device, states=()):
        """Canonical configuration; H owns live state, host owns availability."""
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('MALFORMED_REGISTRY')
                result[key] = value
            return result
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object)
            if not isinstance(data, dict) or set(data) != {'version', 'projects'} or data['version'] != 1 or not isinstance(data['projects'], list):
                raise ValueError('MALFORMED_REGISTRY')
            view = cls([], device)
            view.canonical = True
            seen = {k:set() for k in ('project_id','workspace','h_project_id','git_remote')}
            fields = {'project_id','display_name','aliases','machine_id','workspace','git_remote','h_project_id','project_type','status','last_active_at','next_action','memory_namespace','memory_ref','tags'}
            for row in data['projects']:
                if not isinstance(row, dict) or set(row) != fields: raise ValueError('MALFORMED_REGISTRY')
                for key in fields - {'aliases','tags','last_active_at','memory_namespace','memory_ref'}:
                    if not isinstance(row[key], str) or not row[key].strip(): raise ValueError('MALFORMED_REGISTRY')
                for key in ('aliases','tags'):
                    if not isinstance(row[key], list) or any(not isinstance(v,str) or not v.strip() for v in row[key]): raise ValueError('MALFORMED_REGISTRY')
                for key in ('memory_namespace','memory_ref','last_active_at'):
                    if row[key] is not None and (not isinstance(row[key],str) or not row[key].strip()): raise ValueError('MALFORMED_REGISTRY')
                if row['last_active_at'] is not None: datetime.fromisoformat(row['last_active_at'])
                if row['status'] not in STATUSES or not repo_key(row['git_remote']) or not ntpath.isabs(row['workspace']): raise ValueError('MALFORMED_REGISTRY')
                for key in seen:
                    value = workspace_key(row[key]) if key=='workspace' else repo_key(row[key]) if key=='git_remote' else row[key]
                    if value in seen[key]: raise ValueError('DUPLICATE_'+key.upper())
                    seen[key].add(value)
                p = ProjectRecord(row['project_id'], row['display_name'], aliases=row['aliases'],
                    repo_url=repo_key(row['git_remote']), machine_id=row['machine_id'],workspace=row['workspace'],
                    h_project_id=row['h_project_id'],project_type=row['project_type'],status=row['status'],
                    last_activity=row['last_active_at'],next_action=row['next_action'],
                    memory_namespace=row['memory_namespace'],memory_ref=row['memory_ref'],tags=row['tags'])
                p.sources = [dict(kind='CANONICAL_REGISTRY',ref=str(path))]
                p.availability = 'NOT_ON_THIS_MACHINE'
                if p.machine_id == device.device_id:
                    p.availability = 'UNAVAILABLE'
                    root = Path(p.workspace)
                    if root.is_dir() and any(root.resolve().is_relative_to(Path(r).resolve()) for r in device.trusted_workspace_roots):
                        p.availability = 'AVAILABLE'
                        p.current_machine_available = device.trust_status == 'TRUSTED'
                if p.availability == 'UNAVAILABLE': p.conflicts.append('STALE_WORKSPACE')
                binding = [s for s in states if s.project_id == p.h_project_id]
                if len(binding)>1: raise ValueError('DUPLICATE_H_PROJECT_ID')
                if binding:
                    s = binding[0]
                    if not s.workspace or workspace_key(s.workspace)!=workspace_key(p.workspace): raise ValueError('H_WORKSPACE_CONFLICT')
                    view._states[p.project_id] = replace(s, project_id=p.project_id,project_name=p.canonical_name,device_id=p.machine_id,aliases=tuple(p.aliases))
                    p.h_project_state = asdict(s)
                    p.status = {'RUNNING':'ACTIVE','READY':'ACTIVE','BLOCKED':'WAITING_FOR_INPUT','WAITING_APPROVAL':'WAITING_FOR_INPUT'}.get(s.status,'UNKNOWN')
                    p.last_activity=s.updated_at; p.next_action=s.next_action
                view.records.append(p)
            return view
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError('MALFORMED_REGISTRY') from exc

    def resolve_context(self, reference):
        matches = [p for p in self.records if reference in (p.project_id,p.h_project_id)
            or reference.casefold() in {p.canonical_name.casefold(),*(a.casefold() for a in p.aliases)}
            or (p.workspace and workspace_key(reference)==workspace_key(p.workspace))
            or (repo_key(reference) is not None and repo_key(reference)==p.repo_url)]
        if not matches: return dict(status='NOT_FOUND',workspace=None,execution_allowed=False)
        if len(matches)>1: return dict(status='BLOCKED',reason='AMBIGUOUS_ALIAS',workspace=None,execution_allowed=False)
        p=matches[0]
        available = False
        if p.machine_id==self.device.device_id:
            root=Path(p.workspace)
            available=root.is_dir() and any(root.resolve().is_relative_to(Path(r).resolve()) for r in self.device.trusted_workspace_roots)
            p.current_machine_available=available
            p.availability='AVAILABLE' if available else 'UNAVAILABLE'
        status = 'DEVICE_UNTRUSTED' if self.device.trust_status!='TRUSTED' else 'NOT_ON_THIS_MACHINE' if p.machine_id!=self.device.device_id else 'UNAVAILABLE' if not available else 'RESOLVED'
        return dict(status=status,project_id=p.project_id,h_project_id=p.h_project_id,
            workspace=p.workspace if status=='RESOLVED' else None,machine_id=p.machine_id,execution_allowed=False)

    def __init__(self, rows, device, github_status='UNKNOWN'):
        self.device = device
        self.github_status = github_status
        self.records = []
        self._states = {}
        groups = []
        # Union by explicit repo, stable canonical/H ID or exact workspace on same
        # machine. Names and semantic similarity are never equivalence evidence.
        def keys(row):
            meta = row.get('metadata', {})
            url = repo_key(row.get('repo_url') or meta.get('repo_url'))
            result = {('repo',url)} if url else set()
            pid = row.get('project_id') or meta.get('canonical_id')
            if pid: result.add(('id',pid))
            ws = row.get('workspace') or meta.get('workspace')
            machine = row.get('machine')
            if ws and machine in MACHINES:
                # Remote OS paths stay lexical; never resolve them on this host.
                result.add(('path',machine,str(ws).replace('\\','/').rstrip('/').casefold()))
            return result
        for row in rows:
            ks = keys(row)
            hits = [g for g in groups if ks & g[0]]
            combined = [row]
            for g in hits:
                ks |= g[0]; combined += g[1]; groups.remove(g)
            groups.append((ks,combined))
        for _, group in groups:
            self.records.append(self._record(group))
        names = {}
        for p in self.records:
            for name in [p.canonical_name,*p.aliases]:
                names.setdefault(name.casefold(),set()).add(p.project_id)
        for p in self.records:
            if any(len(names[n.casefold()]) > 1 for n in [p.canonical_name,*p.aliases]):
                p.conflicts.append('NAME_OR_ALIAS_COLLISION; AMBIGUOUS')

    def _record(self, rows):
        hs = [r for r in rows if r['source']=='H']
        gs = [r for r in rows if r['source']=='GITHUB']
        ls = [r for r in rows if r['source']=='LOCAL']
        ms = [r for r in rows if r['source']=='MEMORY']
        anchor = (hs or gs or ls or ms)[0]
        url = next((repo_key(r.get('repo_url') or r.get('metadata',{}).get('repo_url')) for r in rows
                    if repo_key(r.get('repo_url') or r.get('metadata',{}).get('repo_url'))),None)
        pid = anchor.get('project_id') or anchor.get('metadata',{}).get('canonical_id') or identity(
            'repo' if url else 'local' if ls else 'memory', url or (ls[0]['workspace'] if ls else anchor['memory_id']))
        p = ProjectRecord(pid,anchor['name'],repo_url=url,updated_at=datetime.now(timezone.utc).isoformat())
        p.sources = [dict(kind=r['source'],ref=r.get('project_id') or r.get('memory_id') or r.get('repo_url') or r.get('workspace')) for r in rows]
        p.aliases = sorted({a for r in rows for a in r.get('aliases',r.get('metadata',{}).get('aliases',[])) if isinstance(a,str) and a})
        p.machines = sorted({r['machine'] for r in rows if r.get('machine') in MACHINES})
        p.local_workspaces = [dict(machine=r.get('machine'),path=r.get('workspace') or r.get('metadata',{}).get('workspace'),
            verified=r['source']=='LOCAL') for r in rows if r.get('workspace') or r.get('metadata',{}).get('workspace')]
        local_paths = {r['workspace'] for r in ls if r.get('machine')=='WIN10_ADMIN'
            and Path(r['workspace']).is_dir() and any(Path(r['workspace']).resolve().is_relative_to(Path(root).resolve())
                for root in self.device.trusted_workspace_roots)}
        p.current_machine_available = self.device.trust_status=='TRUSTED' and bool(local_paths)
        p.confidence = 1.0 if hs else .9 if gs or ls else .6
        if gs:
            p.github_state = gs[0]['github']; p.repo_visibility = gs[0]['github']['visibility']
            if gs[0]['github']['archived']: p.status='ARCHIVED'
        elif any(r.get('metadata',{}).get('not_on_github') is True for r in ms):
            p.github_state='NOT_ON_GITHUB'
        elif self.github_status=='GITHUB_UNAVAILABLE':
            p.github_state='GITHUB_UNAVAILABLE'
        p.memory_state = [dict(id=r['memory_id'],machine=r.get('machine'),updated_at=r.get('updated_at'),metadata=r.get('metadata',{})) for r in ms]
        for r in ms:
            status=r.get('metadata',{}).get('status')
            if status in STATUSES and p.status=='UNKNOWN': p.status=status
            if r.get('metadata',{}).get('next_action'): p.next_action=r['metadata']['next_action']
        if hs:
            state=hs[0]['state']; p.h_project_state=state
            self._states[pid]=NormalizedProjectState(**{**state,'source_refs':tuple(state['source_refs']),'aliases':tuple(state['aliases']),'activity_at':tuple(state['activity_at'])})
            mapped={'RUNNING':'ACTIVE','READY':'ACTIVE','BLOCKED':'WAITING_FOR_INPUT','WAITING_APPROVAL':'WAITING_FOR_INPUT','COMPLETED':'UNKNOWN','IDLE':'UNKNOWN'}
            p.status=mapped.get(state['status'],'UNKNOWN')
            p.last_activity=state['updated_at']; p.last_valid_state=state['last_valid_state']; p.next_action=state['next_action']
            for r in ms:
                for key,actual in (('status',p.status),('next_action',p.next_action),('workspace',hs[0].get('workspace'))):
                    claim=r.get('metadata',{}).get(key)
                    if claim is not None and claim != actual: p.conflicts.append('MEMORY_H_CONFLICT:'+key)
        if len(hs)>1: p.conflicts.append('MULTIPLE_H_IDENTITIES')
        if not p.current_machine_available and any(m!='WIN10_ADMIN' for m in p.machines):
            if not hs: p.status='WAITING_FOR_DEVICE'
        for w in p.local_workspaces:
            if w['machine']=='WIN10_ADMIN' and w['path'] not in local_paths:
                p.conflicts.append('CURRENT_WORKSPACE_NOT_VERIFIED')
        return p

    def list_projects(self): return list(self.records)
    def get_project(self,name_or_alias):
        if getattr(self,'canonical',False):
            context=self.resolve_context(name_or_alias)
            return [p for p in self.records if p.project_id==context.get('project_id')]
        return [p for p in self.records if name_or_alias.casefold() in {p.project_id.casefold(),p.canonical_name.casefold(),*(a.casefold() for a in p.aliases)}]
    def get_available_projects(self,device_id):
        if getattr(self,'canonical',False):
            return [p for p in self.records if self.resolve_context(p.project_id)['status']=='RESOLVED'] if device_id==self.device.device_id else []
        return [p for p in self.records if p.current_machine_available] if device_id in ('WIN10_ADMIN',self.device.device_id) else []
    def get_projects_waiting_for_device(self,device_id):
        if getattr(self,'canonical',False):
            return [p for p in self.records if p.machine_id==device_id and p.machine_id!=self.device.device_id]
        return [p for p in self.records if p.status=='WAITING_FOR_DEVICE' and device_id in p.machines]
    def resolve_project_reference(self,text):
        matches=[]
        for p in self.records:
            for n in [p.canonical_name,*p.aliases]:
                pattern=rf'(?<![a-z0-9_-]){re.escape(n.casefold())}(?![a-z0-9_-])' if n.isascii() else re.escape(n.casefold())
                if re.search(pattern,text.casefold()): matches.append(p); break
        return matches
    def continue_request(self,request,capsule=None):
        guard=resolve(request,self.device,[])
        if guard.status=='DEVICE_UNTRUSTED': return guard
        if getattr(self,'canonical',False):
            if re.sub(r'\s+', '', request.text) in {'继续昨天那个项目','继续昨天的项目','昨天那个项目','昨天cx做到哪了'}:
                candidates=[s for s in self._states.values() if self.resolve_context(s.project_id)['status']=='RESOLVED']
                return resolve(request,self.device,candidates,capsule)
            context=self.resolve_context(request.text)
            if context['status']=='NOT_FOUND':
                matches=self.resolve_project_reference(request.text)
                if len(matches)>1: return dict(status='BLOCKED',reason='AMBIGUOUS_ALIAS',workspace=None,execution_allowed=False)
                if matches: context=self.resolve_context(matches[0].project_id)
            if context['status']!='RESOLVED': return context
            state=self._states.get(context['project_id'])
            if state is None: return dict(status='BLOCKED',reason='H_BINDING_UNAVAILABLE',workspace=None,execution_allowed=False)
            return resolve(replace(request,text=state.project_name),self.device,[state],capsule)
        matches=self.resolve_project_reference(request.text)
        if not matches:
            # Preserve H task-event calendar semantics for yesterday, no invented activity.
            return resolve(request,self.device,list(self._states.values()),capsule)
        if len(matches)>1 or any('MULTIPLE_H_IDENTITIES' in p.conflicts for p in matches):
            return dict(status='AMBIGUOUS',recommended_adapter='NO_VALID_AGENT',execution_allowed=False,
                        ambiguity_candidates=[p.project_id for p in matches])
        p=matches[0]
        if p.current_machine_available and p.project_id in self._states:
            return resolve(request,self.device,[self._states[p.project_id]],capsule)
        trusted=self.device.trust_status=='TRUSTED'
        reason='WAITING_FOR_DEVICE' if p.status=='WAITING_FOR_DEVICE' else 'NO_CURRENT_H_BINDING_OR_WORKSPACE'
        return dict(status='RESOLVED' if trusted else 'DEVICE_UNTRUSTED',project_id=p.project_id,
            project_name=p.canonical_name,workspace=None,current_machine_available=p.current_machine_available,
            recommended_adapter='NO_VALID_AGENT',reason=reason,execution_allowed=False,
            source_refs=p.sources,device_id=self.device.device_id,last_valid_state=p.last_valid_state,
            last_task=None,last_result=None,next_candidate_action=p.next_action,confidence=p.confidence,
            ambiguity_candidates=[],device_context=asdict(self.device))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-home',type=Path,required=True)
    parser.add_argument('--root',type=Path,action='append',required=True)
    parser.add_argument('--github-owner')
    parser.add_argument('--registry-file',type=Path,default=DEFAULT_REGISTRY)
    parser.add_argument('--gh',type=Path,default=Path('C:/Program Files/GitHub CLI/gh.exe'))
    parser.add_argument('--memory-root',type=Path)
    parser.add_argument('--memory-python',type=Path,default=Path(sys.executable))
    parser.add_argument('--memory-store',type=Path,action='append',default=[])
    parser.add_argument('--continue-text')
    args=parser.parse_args()
    device=local_device(args.hermes_home,args.root)
    if not args.github_owner:
        try:
            view=ProjectRegistry.load(args.registry_file,device,read_states(args.hermes_home))
            result=view.continue_request(ContinueRequest(args.continue_text)) if args.continue_text else None
            print(json.dumps(dict(projects=[asdict(p) for p in view.records],continuation=asdict(result) if isinstance(result,ContinueResolution) else result),ensure_ascii=False,indent=2))
            return 0
        except (OSError,ValueError):
            print(json.dumps(dict(status='BLOCKED',reason='REGISTRY_OR_SOURCE_INVALID')))
            return 2
    h=h_source(args.hermes_home)
    local=local_source(args.root,[r['workspace'] for r in h if r['workspace']])
    github,gstatus=github_source(args.github_owner,args.gh)
    memory,mstatus=memory_source(args.memory_root,args.memory_python,args.memory_store) if args.memory_root and args.memory_store else ([], 'NOT_CONFIGURED')
    view=ProjectRegistry(h+local+github+memory,device,gstatus)
    result=ProjectRegistry.load(args.registry_file,device,read_states(args.hermes_home)).continue_request(ContinueRequest(args.continue_text)) if args.continue_text else None
    print(json.dumps(dict(projects=[asdict(p) for p in view.list_projects()],github_source=gstatus,
        memory_source=mstatus,continuation=asdict(result) if isinstance(result,ContinueResolution) else result),ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())

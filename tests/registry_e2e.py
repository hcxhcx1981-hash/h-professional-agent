"""Read-only real H -> canonical Registry -> Continue Resolver acceptance."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from hpa.continuation import ContinueRequest, local_device, read_states, readonly_db
from hpa.registry import DEFAULT_REGISTRY, ProjectRegistry, repo_key, workspace_key


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-home',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    args=parser.parse_args()
    source=args.hermes_home/'hermes-agent'
    if not (source/'hermes_cli'/'projects_db.py').is_file(): raise ValueError('H_INTERFACE_MISSING')
    sys.path.insert(0,str(source))
    from hermes_cli.projects_db import get_project
    files=[args.hermes_home/(name+suffix) for name in ('projects.db','kanban.db') for suffix in ('','-wal','-shm')]
    def hashes(): return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    before=hashes()
    device=local_device(args.hermes_home,[args.workspace])
    states=read_states(args.hermes_home)
    view=ProjectRegistry.load(DEFAULT_REGISTRY,device,states)
    matches=[p for p in view.records if workspace_key(p.workspace)==workspace_key(str(args.workspace.resolve()))]
    assert len(matches)==1, 'CANONICAL_IDENTITY_MISSING_OR_AMBIGUOUS'
    p=matches[0]
    with readonly_db(args.hermes_home/'projects.db') as conn:
        actual=get_project(conn,p.h_project_id)
        assert actual is not None and actual.id==p.h_project_id
        assert workspace_key(actual.primary_path)==workspace_key(p.workspace)
        assert actual.name==p.canonical_name
    remote=subprocess.run(['git','-C',str(args.workspace),'config','--get','remote.origin.url'],check=True,capture_output=True,text=True).stdout.strip()
    assert repo_key(remote)==p.repo_url and p.machine_id==device.device_id
    for ref in (p.project_id,p.canonical_name,*p.aliases,p.workspace,p.h_project_id):
        result=view.continue_request(ContinueRequest(ref))
        assert result.status=='RESOLVED' and workspace_key(result.workspace)==workspace_key(p.workspace)
    assert before==hashes(), 'H_SOURCE_CHANGED'
    print(json.dumps(dict(real_e2e='PASS',h_project_id=actual.id,machine_id=device.device_id,
        workspace=result.workspace,h_status=p.h_project_state['status'],source_bytes_unchanged=True),ensure_ascii=False))


if __name__=='__main__': main()

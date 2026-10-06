"""Real Win10 H record -> Registry -> natural-language router -> handoff; no dispatch."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess

from hpa.capability import route_task
from hpa.continuation import local_device,read_states
from hpa.registry import DEFAULT_REGISTRY,ProjectRegistry,repo_key,workspace_key


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-home',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    args=parser.parse_args()
    files=[args.hermes_home/(name+suffix) for name in ('projects.db','kanban.db') for suffix in ('','-wal','-shm')]
    def hashes(): return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    before=hashes()
    device=local_device(args.hermes_home,[args.workspace])
    states=read_states(args.hermes_home)
    registry=ProjectRegistry.load(DEFAULT_REGISTRY,device,states)
    text='继续开发 h-professional-agent，给当前项目增加一个需要跨文件修改、测试和 Git 收口的正式功能。'
    result=route_task(text,registry,acceptance_criteria=('相关自动测试和 wheel 构建通过','检查差异，仅提交本任务文件，Git clean'),
        relevant_paths=('hpa/capability.py','tests/test_task_routing.py','docs/TASK_ROUTING.md'))
    assert result.route_target=='CX' and result.requires_user_approval and not result.execution_attempted
    package=result.handoff_package
    assert package and workspace_key(package['workspace'])==workspace_key(str(args.workspace.resolve()))
    assert package['machine_id']==device.device_id and package['target_agent']=='CX'
    real=[s for s in states if s.project_id==package['project_id']]
    assert len(real)==1 and workspace_key(real[0].workspace)==workspace_key(package['workspace'])
    record=next(p for p in registry.records if p.project_id==package['project_id'])
    remote=subprocess.run(['git','-C',str(args.workspace),'config','--get','remote.origin.url'],capture_output=True,text=True,check=True).stdout.strip()
    assert record.repo_url==repo_key(remote)
    assert before==hashes(), 'H_SOURCE_CHANGED'
    assert not package['execution_allowed']
    print(json.dumps(dict(real_e2e='PASS',h_source_bytes_unchanged=True,**asdict(result)),ensure_ascii=False,indent=2))


if __name__=='__main__': main()

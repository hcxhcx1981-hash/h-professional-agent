from dataclasses import asdict, replace
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hpa.capability import TaskProfile,build_task_profile,route_task
from hpa.continuation import DeviceContext,NormalizedProjectState
from hpa.registry import DEFAULT_REGISTRY,ProjectRegistry
from tests.test_capability import _make_capsule
from hpa.core import Project


class TaskRoutingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.device=DeviceContext('win10','test','Windows-10','Admin',(str(self.root),),(),
            datetime.now(timezone.utc).isoformat(),'fixture',1.0)
        self.row=json.loads(DEFAULT_REGISTRY.read_text(encoding='utf-8'))['projects'][0]
        self.row.update(machine_id='win10',workspace=str(self.root))
        self.state=NormalizedProjectState(self.row['project_id'],self.row['display_name'],str(self.root),None,
            'IDLE',None,None,None,'检查当前项目状态',None,('H/projects/'+self.row['project_id'],))
        self.file=self.root/'registry.json'
        self.load()
        self.acceptance=('相关测试通过并报告结果',)

    def load(self):
        self.file.write_text(json.dumps(dict(version=1,projects=[self.row])),encoding='utf-8')
        self.view=ProjectRegistry.load(self.file,self.device,[self.state])

    def route(self,text,**kwargs):
        kwargs.setdefault('acceptance_criteria',self.acceptance)
        kwargs.setdefault('relevant_paths',('hpa/capability.py','tests'))
        return route_task(text,self.view,self.row['project_id'],**kwargs)

    def test_a_python_development(self):
        self.assertEqual(self.route('继续修改 h-professional-agent 的 Python 实现并跑测试').route_target,'CX')

    def test_b_research(self):
        result=self.route('搜索最近一个月某 Agent 框架资料并整理报告')
        self.assertEqual(result.route_target,'WB')
        self.assertIn('CONSERVE_CX_QUOTA',result.reasons[0])

    def test_c_project_status(self):
        result=self.route('检查当前项目状态、读取 Registry 并告诉我下一步')
        self.assertEqual(result.route_target,'SELF'); self.assertIsNone(result.handoff_package)

    def test_d_mineru_verified_h_skill(self):
        claim=dict(verified=True,low_risk=True,project_id=self.row['project_id'],machine_id='win10',
            task_type='DOCUMENT_ANALYSIS',skill_id='mineru',evidence_ref='verified-install-and-task-fit')
        result=self.route('用 MinerU 分析复杂 PDF',verified_skills=[claim])
        self.assertEqual(result.route_target,'SELF')
        self.assertEqual(self.route('用 MinerU 分析复杂 PDF').route_target,'BLOCKED')

    def test_e_cross_file_refactor(self):
        self.assertEqual(self.route('对当前仓库做正式跨文件重构并提交').route_target,'CX')

    def test_f_explicit_ac_suitability(self):
        facts=dict(task_type='ENGINEERING',requires_local_code_change=True,requires_deep_engineering=False,
            requires_git_operations=False,estimated_complexity='LOW',risk_level='LOW',preferred_agent='AC')
        claim=dict(verified=True,project_id=self.row['project_id'],machine_id='win10',task_type='ENGINEERING',evidence_ref='controlled-ac-acceptance')
        result=self.route('做一个明确、低风险、适合 AC 的代码修改任务',characteristics=facts,ac_suitability=claim)
        self.assertEqual(result.route_target,'AC')
        self.assertEqual(self.route('受控代码修改',characteristics=facts).route_target,'BLOCKED')
        self.assertEqual(self.route('受控代码修改',characteristics={**facts,'requires_deep_engineering':True},ac_suitability=claim).route_target,'BLOCKED')

    def test_g_machine_isolation(self):
        self.row['machine_id']='win11'; self.load()
        result=self.route('对当前仓库做正式跨文件重构并提交')
        self.assertEqual(result.route_target,'BLOCKED'); self.assertIn('NOT_ON_THIS_MACHINE',result.blocked_reasons)

    def test_h_unknown_project(self):
        self.assertEqual(route_task('检查当前项目状态',self.view,'unknown').route_target,'BLOCKED')

    def test_i_decomposition(self):
        result=self.route('先搜索最近一个月 Agent 框架资料，再开发跨文件正式功能')
        self.assertEqual(result.route_target,'BLOCKED'); self.assertEqual(result.decision,'NEEDS_DECOMPOSITION')
        self.assertEqual(result.recommended_sequence,('WB','CX'))

    def test_j_wrong_preference(self):
        self.assertEqual(self.route('对当前仓库做正式跨文件重构并提交',characteristics={'preferred_agent':'WB'}).route_target,'BLOCKED')

    def test_bare_nouns_and_unknown_fields(self):
        for text in ('代码','搜索','我们聊聊某件事'):
            self.assertEqual(self.route(text).route_target,'BLOCKED')
        profile,_=build_task_profile('代码')
        self.assertIsNone(profile.requires_local_code_change); self.assertEqual(profile.risk_level,'UNKNOWN')

    def test_approval_determinism_and_no_execution(self):
        with patch('subprocess.run',side_effect=AssertionError('Agent execution forbidden')):
            first=self.route('对当前仓库做正式跨文件重构并提交')
            second=self.route('对当前仓库做正式跨文件重构并提交')
        self.assertEqual(asdict(first),asdict(second))
        self.assertTrue(first.requires_user_approval); self.assertFalse(first.execution_attempted)
        package=first.handoff_package
        required={'handoff_id','project_id','machine_id','workspace','target_agent','objective','context',
            'current_state','allowed_scope','hard_boundaries','acceptance_criteria','relevant_paths',
            'relevant_refs','expected_return_format'}
        self.assertTrue(required<=set(package)); self.assertFalse(package['execution_allowed'])

    def test_forbidden_agent_and_sensitive_access(self):
        self.assertEqual(self.route('对当前仓库做正式跨文件重构并提交',characteristics={'forbidden_agents':['CX']}).route_target,'BLOCKED')
        self.assertEqual(self.route('读取凭据再修改代码',characteristics={'risk_level':'LOW'}).route_target,'BLOCKED')
        self.assertEqual(self.route('删除仓库',characteristics={'risk_level':'LOW'}).route_target,'BLOCKED')

    def test_minimal_handoff_scope(self):
        for paths in ((),('.',),('../outside',),('.env',),('.git/config',)):
            self.assertEqual(self.route('对当前仓库做正式跨文件重构并提交',relevant_paths=paths).route_target,'BLOCKED')
        self.assertEqual(self.route('对当前仓库做正式跨文件重构并提交',acceptance_criteria=()).route_target,'BLOCKED')

    def test_state_next_action(self):
        result=self.route('')
        self.assertEqual(result.route_target,'SELF')
        self.assertTrue(any(e.get('source')=='H_PROJECT_STATE' for e in result.evidence))

    def test_capsule_policy_and_scope(self):
        cap=Project.load(_make_capsule(self.root,self.row['project_id'],['external_agent'],['read_file']))
        self.assertEqual(self.route('检查当前项目状态',capsule=cap).route_target,'SELF')
        self.assertEqual(self.route('对当前仓库做正式跨文件重构并提交',capsule=cap).route_target,'BLOCKED')

    def test_profile_validation(self):
        for facts in ({'risk_level':'UNSAFE'},{'requires_local_code_change':'false'}, {'preferred_agent':'OTHER'}, {'unexpected':True}):
            self.assertEqual(self.route('检查当前项目状态',characteristics=facts).route_target,'BLOCKED')

    def test_stale_workspace(self):
        self.view.records[0].workspace=str(self.root/'missing')
        self.assertEqual(self.route('检查当前项目状态').route_target,'BLOCKED')

    def test_operation_context_and_safe_overrides(self):
        self.assertEqual(self.route('读取当前项目的 Memory').route_target,'SELF')
        self.assertEqual(self.route('对当前仓库运行测试并构建').route_target,'CX')
        self.assertEqual(self.route('调整简单配置').route_target,'WB')
        self.assertEqual(self.route('读取凭据再修改代码',characteristics={'requires_sensitive_access':False}).route_target,'BLOCKED')
        result=self.route('检查当前项目状态',source_requirements=['sk-'+'x'*24])
        self.assertEqual(result.route_target,'BLOCKED'); self.assertNotIn('x'*24,json.dumps(asdict(result)))


if __name__=='__main__': unittest.main()

import hashlib
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from hpa.continuation import ContinueRequest, DeviceContext, NormalizedProjectState
from hpa.registry import ProjectRegistry, local_source, github_source, repo_key

class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.device=DeviceContext('host-id','test','Windows-10','test',(str(self.root),),
            ('H_NATIVE','CX'),datetime.now(timezone.utc).isoformat(),'fixture',1.0)
        self.url='https://github.com/example/project'
        self.state=NormalizedProjectState('p_real','project',str(self.root),None,'RUNNING',None,None,
            {'status':'COMPLETED'},'query',None,('H/projects/p_real',),(),())
        self.h=dict(source='H',project_id='p_real',name='project',machine='WIN10_ADMIN',workspace=str(self.root),state=asdict(self.state))
        self.local=dict(source='LOCAL',name='project',machine='WIN10_ADMIN',workspace=str(self.root),repo_url=self.url)
        self.github=dict(source='GITHUB',name='project',repo_url=self.url,github=dict(visibility='PRIVATE',archived=False))
        self.memory=dict(source='MEMORY',name='old-name',machine='WIN10_ADMIN',memory_id='m1',updated_at='2020-01-01',
            metadata=dict(repo_url=self.url,status='PAUSED',aliases=['alias'],next_action='old'))
    def view(self,rows): return ProjectRegistry(rows,self.device,'COMPLETE')
    def test_four_sources_merge(self):
        v=self.view([self.h,self.local,self.github,self.memory]); p=v.list_projects()[0]
        self.assertEqual(len(v.records),1); self.assertEqual(len(p.sources),4)
        self.assertEqual(p.project_id,'p_real'); self.assertTrue(p.current_machine_available)
    def test_github_only(self):
        p=self.view([self.github]).records[0]
        self.assertFalse(p.current_machine_available); self.assertEqual(p.status,'UNKNOWN')
    def test_not_on_github(self):
        p=self.view([{**self.memory,'metadata':{'not_on_github':True}}]).records[0]
        self.assertEqual(p.github_state,'NOT_ON_GITHUB')
    def test_other_device_continue(self):
        m={**self.memory,'machine':'MAC_WIN10','metadata':dict(workspace='/projects/one',aliases=['市场监管'])}
        v=self.view([m]); p=v.records[0]
        self.assertEqual(p.status,'WAITING_FOR_DEVICE')
        r=v.continue_request(ContinueRequest('继续市场监管'))
        self.assertEqual(r['status'],'RESOLVED'); self.assertEqual(r['recommended_adapter'],'NO_VALID_AGENT')
        self.assertEqual(r['reason'],'WAITING_FOR_DEVICE'); self.assertIsNone(r['workspace'])
    def test_same_name_ambiguous(self):
        g={**self.github,'repo_url':'https://github.com/other/project'}
        v=self.view([self.github,g]); r=v.continue_request(ContinueRequest('继续 project'))
        self.assertEqual(r['status'],'AMBIGUOUS'); self.assertEqual(len(v.records),2)
    def test_remote_normalization(self):
        self.assertEqual(repo_key('git@github.com:Example/Project.git'),self.url)
        self.assertIsNone(repo_key('https://token@github.com/example/project'))
        self.assertEqual(len(self.view([self.local,self.github]).records),1)
    def test_h_state_wins(self):
        p=self.view([self.h,self.local,self.memory]).records[0]
        self.assertEqual(p.status,'ACTIVE'); self.assertEqual(p.next_action,'query')
        self.assertIn('MEMORY_H_CONFLICT:status',p.conflicts)
    def test_missing_workspace(self):
        p=self.view([self.h,self.memory]).records[0]
        self.assertFalse(p.current_machine_available)
        self.assertIn('CURRENT_WORKSPACE_NOT_VERIFIED',p.conflicts)
    def test_registry_readonly_queries(self):
        (self.root/'README.md').write_text('test'); (self.root/'pyproject.toml').write_text('')
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()}
        rows=local_source([self.root]); v=self.view(rows)
        v.list_projects(); v.get_project(self.root.name); v.get_available_projects('host-id')
        v.get_projects_waiting_for_device('WIN11_LENOVO'); v.resolve_project_reference(self.root.name)
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()})
    def test_unknown_machine_not_available(self):
        p=self.view([{**self.memory,'machine':'unverified'}]).records[0]
        self.assertEqual(p.machines,[]); self.assertFalse(p.current_machine_available)
    def test_untrusted_host_blocks_resolution(self):
        v=ProjectRegistry([self.memory],replace(self.device,trust_status='UNTRUSTED'))
        self.assertEqual(v.continue_request(ContinueRequest('old-name')).status,'DEVICE_UNTRUSTED')
    def test_github_unavailable_no_retry(self):
        with patch('hpa.registry.command',side_effect=OSError) as c:
            self.assertEqual(github_source('example',Path('gh')),([], 'GITHUB_UNAVAILABLE'))
            self.assertEqual(c.call_count,1)
    def test_no_semantic_merge(self):
        m={**self.memory,'name':'project','metadata':{}}
        self.assertEqual(len(self.view([self.github,m]).records),2)
    def test_local_shallow(self):
        child=self.root/'child'; child.mkdir(); (child/'README.md').write_text('')
        deep=child/'deep'; deep.mkdir(); (deep/'README.md').write_text(''); (deep/'pyproject.toml').write_text('')
        self.assertEqual(local_source([self.root]),[])
    def test_no_github_claim_when_unavailable(self):
        v=ProjectRegistry([{**self.local,'repo_url':None}],self.device,'GITHUB_UNAVAILABLE')
        self.assertEqual(v.records[0].github_state,'GITHUB_UNAVAILABLE')

if __name__=='__main__': unittest.main()

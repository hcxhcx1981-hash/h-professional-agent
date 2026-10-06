from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
import tempfile
from hpa.registry import ProjectRegistry, DEFAULT_REGISTRY
from hpa.continuation import ContinueRequest, DeviceContext, NormalizedProjectState


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.device=DeviceContext('host-id','test','Windows-10','test',(str(self.root),),
            (),datetime.now(timezone.utc).isoformat(),'fixture',1.0)
        self.state=NormalizedProjectState('p_real','project',str(self.root),None,'IDLE',None,None,
            None,'query',None,('H/projects/p_real',))
        self.row=json.loads(DEFAULT_REGISTRY.read_text(encoding='utf-8'))['projects'][0]
        self.row.update(project_id='p_real',h_project_id='p_real',machine_id=self.device.device_id,
                        workspace=str(self.root),display_name='project',aliases=['alias'])
        self.file=self.root/'registry.json'

    def load(self, rows=None, device=None, states=()):
        self.file.write_text(json.dumps({'version':1,'projects':rows or [self.row]}),encoding='utf-8')
        return ProjectRegistry.load(self.file,device or self.device,states)

    def test_canonical_load(self):
        self.assertEqual(self.load().records[0].project_id,'p_real')

    def test_exact_identifiers(self):
        self.row['h_project_id']='h_other'
        v=self.load()
        for reference in ('p_real','project','alias',str(self.root),'h_other',self.row['git_remote']):
            with self.subTest(reference=reference):
                self.assertEqual(v.resolve_context(reference)['status'],'RESOLVED')

    def test_machine_mismatch(self):
        v=self.load(device=replace(self.device,device_id='other'))
        r=v.resolve_context('p_real')
        self.assertEqual(r['status'],'NOT_ON_THIS_MACHINE'); self.assertIsNone(r['workspace'])
        self.assertEqual(v.get_available_projects('WIN10_ADMIN'),[])
        self.assertEqual(v.continue_request(ContinueRequest('继续 alias'))['status'],'NOT_ON_THIS_MACHINE')

    def test_ambiguous_alias(self):
        other={**self.row,'project_id':'other','h_project_id':'other','workspace':str(self.root/'other'),'git_remote':'https://github.com/example/other'}
        self.assertEqual(self.load([self.row,other]).resolve_context('alias')['status'],'BLOCKED')

    def test_stale_workspace(self):
        self.row['workspace']=str(self.root/'missing')
        v=self.load(); self.assertIn('STALE_WORKSPACE',v.records[0].conflicts)
        self.assertEqual(v.resolve_context('p_real')['status'],'UNAVAILABLE')

    def test_malformed(self):
        for content in ('{','[]','{"version":1,"projects":{}}','{"version":1,"version":1,"projects":[]}'):
            self.file.write_text(content,encoding='utf-8')
            with self.assertRaises(ValueError): ProjectRegistry.load(self.file,self.device)
        for key,value in (('aliases','alias'),('machine_id',None),('tags',[1]),('git_remote','https://token@github.com/example/repo')):
            row={**self.row,key:value}
            with self.assertRaises(ValueError): self.load([row])

    def test_unknown(self):
        v=self.load(states=[self.state])
        self.assertEqual(v.resolve_context('unknown')['status'],'NOT_FOUND')
        self.assertEqual(v.continue_request(ContinueRequest('继续 unknown'))['status'],'NOT_FOUND')

    def test_duplicates(self):
        for key in ('project_id','workspace','h_project_id','git_remote'):
            other={**self.row,'project_id':'other','h_project_id':'other','workspace':str(self.root/'other'),'git_remote':'https://github.com/example/other'}
            other[key]=self.row[key]
            with self.subTest(key=key),self.assertRaises(ValueError): self.load([self.row,other])

    def test_resolver_connection(self):
        v=self.load(states=[self.state])
        for reference in ('p_real','alias',str(self.root)):
            self.assertEqual(v.continue_request(ContinueRequest(reference)).workspace,str(self.root))

    def test_h_binding_conflict(self):
        with self.assertRaises(ValueError): self.load(states=[replace(self.state,workspace=str(self.root/'wrong'))])

if __name__=='__main__': unittest.main()

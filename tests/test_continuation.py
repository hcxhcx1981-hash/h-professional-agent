"""All databases and Capsule fixtures are temporary; never seed a real H home."""
from dataclasses import asdict, replace
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hpa.core import Project
from hpa.continuation import (ContinueRequest, DeviceContext, NormalizedProjectState,
                              read_states, readonly_db, resolve, with_memory, infer_intent)


@contextmanager
def fixture_db(path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'h'
        self.home.mkdir()
        self.now = datetime.now(timezone.utc)
        self.device = DeviceContext('local-win10', 'fixture-host', 'Windows-10', 'fixture-user',
            (str(self.root),), ('H_NATIVE', 'CX'), self.now.isoformat(), 'fixture-trusted-host', 1.0)
        with fixture_db(self.home / 'projects.db') as c:
            c.executescript('CREATE TABLE projects(id,name,slug,primary_path,board_slug,archived);'
                           'CREATE TABLE project_folders(project_id,path,is_primary);')
        with fixture_db(self.home / 'kanban.db') as c:
            c.execute('CREATE TABLE tasks(id,project_id,status,created_at,started_at,completed_at,result)')
        self.project = self.add_project('personal', 'Personal Agent', '个人助手')
        self.capsule = self.make_capsule(self.project)

    def add_project(self, id, name, alias):
        workspace = self.root / id
        workspace.mkdir()
        with fixture_db(self.home / 'projects.db') as c:
            c.execute('INSERT INTO projects VALUES(?,?,?,?,?,0)', (id,name,alias,str(workspace),None))
        return read_states(self.home)[-1]

    def make_capsule(self, p):
        path = Path(p.workspace) / 'project-capsule.json'
        path.write_text(json.dumps(dict(version='0.1',project_id=p.project_id,
            project_name=p.project_name,project_root='.',allowed_skills=['inspect'],
            allowed_tools=['read_file','terminal'],memory_namespace='synthetic/'+p.project_id,
            artifact_workspace='artifacts',created_at=self.now.isoformat())), encoding='utf-8')
        return Project.load(path)

    def run_query(self, text, intent='status', states=None, device=None):
        return resolve(ContinueRequest(text, self.now, intent), device or self.device,
                       states if states is not None else read_states(self.home), self.capsule)

    def activity(self, project):
        yesterday = int((self.now - timedelta(days=1)).timestamp())
        with fixture_db(self.home / 'kanban.db') as c:
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',
                      ('task-'+project,project,'done',yesterday,yesterday,yesterday,'fixture-result'))

    def test_01_unique_name_resolved(self):
        r = self.run_query('接着搞 Personal Agent')
        self.assertEqual(r.status, 'RESOLVED')
        self.assertEqual(r.project_id, 'personal')
        self.assertEqual(r.workspace, self.project.workspace)
        self.assertFalse(r.routing.execution_allowed)

    def test_02_yesterday_unique(self):
        self.activity('personal')
        r = self.run_query('继续昨天那个项目')
        self.assertEqual(r.status, 'RESOLVED')
        self.assertEqual(r.last_result['task_id'], 'task-personal')

    def test_03_yesterday_ambiguous(self):
        self.add_project('memory', 'Memory OS', '记忆系统')
        self.activity('personal')
        self.activity('memory')
        r = self.run_query('昨天 CX 做到哪了')
        self.assertEqual(r.status, 'AMBIGUOUS')
        self.assertEqual(len(r.ambiguity_candidates), 2)
        self.assertIsNone(r.workspace)

    def test_04_not_found(self):
        r = self.run_query('继续不存在的项目 C:/invented/path')
        self.assertEqual(r.status, 'NOT_FOUND')

    def test_05_other_device(self):
        r = self.run_query('Personal Agent', states=[replace(self.project,device_id='win11')])
        self.assertEqual(r.status, 'DEVICE_UNTRUSTED')
        self.assertIsNone(r.workspace)
        self.assertEqual(r.reason, 'PROJECT_DEVICE_MISMATCH')

    def test_06_query_native(self):
        self.assertEqual(self.run_query('Personal Agent 状态整理').recommended_adapter, 'H_NATIVE')

    def test_07_development_codex_recommendation_only(self):
        r = self.run_query('Personal Agent', 'develop')
        self.assertEqual(r.recommended_adapter, 'CX')
        self.assertFalse(r.routing.execution_allowed)
        self.assertEqual(r.routing.availability, 'NOT_PROBED_FOR_EXECUTION')

    def test_08_unknown_capability(self):
        self.assertEqual(self.run_query('Personal Agent', 'unknown').recommended_adapter, 'NO_VALID_AGENT')
        self.assertEqual(infer_intent('Personal Agent quantum teleport'),'unknown')
        self.assertEqual(infer_intent('继续 Personal Agent 转账'),'unknown')
        self.assertEqual(infer_intent('Personal Agent 修改代码'),'develop')

    def test_09_memory_cannot_replace_state(self):
        self.activity('personal')
        r = self.run_query('Personal Agent')
        # Background is a distinct field; it is never parsed as project identity/state.
        with patch('hpa.continuation.memory_context', return_value={'context':'Old memory: project is blocked'}):
            r = with_memory(r,self.capsule,self.root,self.root/'python','workflow')
        self.assertEqual(r.last_valid_state['status'], 'COMPLETED')
        self.assertEqual(r.last_result['source'], 'H/Kanban')

    def test_10_readonly_bytes_and_sql(self):
        before = {p:hashlib.sha256(p.read_bytes()).digest() for p in self.home.iterdir()}
        with patch('subprocess.run', side_effect=AssertionError('No process calls')):
            self.run_query('Personal Agent')
        self.assertEqual(before, {p:hashlib.sha256(p.read_bytes()).digest() for p in self.home.iterdir()})
        with readonly_db(self.home / 'projects.db') as c:
            with self.assertRaises(sqlite3.OperationalError):
                c.execute("INSERT INTO projects(id) VALUES('forbidden')")

    def test_alias_is_from_h(self):
        self.assertEqual(self.run_query('继续个人助手').status, 'RESOLVED')

    def test_stale_device_and_missing_adapter(self):
        stale = replace(self.device, last_seen=(self.now-timedelta(seconds=61)).isoformat())
        self.assertEqual(self.run_query('Personal Agent',device=stale).status,'DEVICE_UNTRUSTED')
        self.assertEqual(self.run_query('Personal Agent',device=replace(self.device,available_adapters=())).recommended_adapter,'NO_VALID_AGENT')

    def test_missing_workspace_and_root_escape(self):
        p = replace(self.project,workspace=str(self.root/'missing'))
        self.assertEqual(self.run_query('Personal Agent',states=[p]).status,'DEVICE_UNTRUSTED')
        d = replace(self.device,trusted_workspace_roots=(str(self.home),))
        self.assertEqual(self.run_query('Personal Agent',device=d).status,'DEVICE_UNTRUSTED')

    def test_capsule_and_tools_required(self):
        r = resolve(ContinueRequest('Personal Agent',self.now), self.device,[self.project])
        self.assertEqual(r.recommended_adapter,'NO_VALID_AGENT')
        capsule = replace(self.capsule,data={**self.capsule.data,'allowed_tools':[]})
        r = resolve(ContinueRequest('Personal Agent',self.now), self.device,[self.project],capsule)
        self.assertEqual(r.recommended_adapter,'NO_VALID_AGENT')

    def test_public_memory_interface_is_readonly(self):
        with patch('hpa.continuation.memory_context', return_value={'context':'fixture background'}) as read:
            result = with_memory(self.run_query('Personal Agent'),self.capsule,self.root,self.root/'python','workflow')
            self.assertEqual(read.call_args.args[-1],self.capsule.root/'synthetic-memory.json')
            self.assertEqual(result.memory_background['authority'],'BACKGROUND_ONLY')

    def test_unregistered_path_never_project(self):
        self.assertEqual(self.run_query(str(self.project.workspace)).status,'NOT_FOUND')
        self.activity('personal')
        self.assertEqual(self.run_query('继续昨天不存在的另一个项目').status,'NOT_FOUND')

    def test_wal_snapshot_keeps_committed_state_without_touching_source(self):
        path = self.root / 'wal.db'
        writer = sqlite3.connect(path)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE evidence(value)')
        writer.execute("INSERT INTO evidence VALUES('committed-in-wal')")
        writer.commit()
        files = [path,Path(str(path)+'-wal'),Path(str(path)+'-shm')]
        before = {p:hashlib.sha256(p.read_bytes()).digest() for p in files}
        with readonly_db(path) as c:
            self.assertEqual(c.execute('SELECT value FROM evidence').fetchone()[0],'committed-in-wal')
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).digest() for p in files})


if __name__ == '__main__':
    unittest.main()

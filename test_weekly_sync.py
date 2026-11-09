import datetime as dt
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from export_rules import read_active_rules
from install_weekly_sync import build_plist
from weekly_sync import SNAPSHOT,SyncError,atomic_json,latest_slot,run_job,validate_config

class WeeklySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='weekly-feedback-test-');self.root=Path(self.tmp.name)
        self.state=self.root/'learning';self.state.mkdir()
        self.db=sqlite3.connect(self.state/'feedback.sqlite3')
        self.db.execute('CREATE TABLE rules(rule_id TEXT PRIMARY KEY,payload TEXT)')
        self.db.execute('CREATE TABLE original_conversations(raw_text TEXT)')
        self.db.execute('INSERT INTO original_conversations VALUES(?)',('PRIVATE-CONVERSATION-MUST-STAY-LOCAL',))
        self.rule={'id':'R1','scope':'global','text':'先明确实际目标，再选择实现方式。','status':'active','explicit_long_term':True,'support':['private-session-marker'],'evidence':['private-evidence-marker']}
        self.save_rule()
        self.remote=self.root/'remote.git';self.origin=self.root/'source'
        self.git('init','--bare',str(self.remote),cwd=self.root)
        self.git('init','-b','main',str(self.origin),cwd=self.root)
        self.git('config','user.name','Example User');self.git('config','user.email','example@users.noreply.github.com')
        target=self.origin/SNAPSHOT;target.parent.mkdir(parents=True)
        target.write_text(json.dumps(read_active_rules(self.state),ensure_ascii=False,indent=2)+'\n')
        (self.origin/'README.md').write_text('Original documentation\n')
        self.git('add','.');self.git('commit','-m','Initial fixture')
        self.git('remote','add','origin',str(self.remote));self.git('push','origin','main')
        self.config={'repository':str(self.remote),'branch':'main','state_dir':str(self.state),'work_dir':str(self.root/'job'),
                     'git':shutil.which('git'),'gh':'/nonexistent/unused-for-file-transport',
                     'author_name':'Example User','author_email':'example@users.noreply.github.com',
                     'timezone':'Asia/Shanghai','weekday':0,'hour':10,'minute':0}
        self.now=dt.datetime(2026,11,9,3,0,tzinfo=dt.timezone.utc)
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def save_rule(self):
        self.db.execute('DELETE FROM rules');self.db.execute('INSERT INTO rules VALUES(?,?)',(self.rule['id'],json.dumps(self.rule,ensure_ascii=False)));self.db.commit()
    def git(self,*args,cwd=None):
        r=subprocess.run(['git',*args],cwd=cwd or getattr(self,'origin',self.root),capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);return r.stdout.strip()
    def head(self):return self.git('--git-dir',str(self.remote),'rev-parse','main',cwd=self.root)
    def change_rule(self):
        self.rule['text']='目标环境未明确时先简短询问，已明确时直接沿用。';self.save_rule()
    def test_monday_beijing_schedule_and_boundary(self):
        before=dt.datetime(2026,11,9,1,59,tzinfo=dt.timezone.utc)
        at=dt.datetime(2026,11,9,2,0,tzinfo=dt.timezone.utc)
        self.assertEqual(latest_slot(before,self.config).isoformat(),'2026-11-02T10:00:00+08:00')
        self.assertEqual(latest_slot(at,self.config).isoformat(),'2026-11-09T10:00:00+08:00')
        plist=build_plist(self.config,'/usr/bin/python3',self.root/'runner.py')
        self.assertEqual(plist['StartCalendarInterval'],{'Weekday':1,'Hour':10,'Minute':0})
        self.assertTrue(plist['RunAtLoad']);self.assertEqual(plist['StartInterval'],3600)
    def test_no_changes_does_not_create_commit(self):
        head=self.head();result=run_job(self.config,self.now)
        self.assertEqual(result['result'],'no_change');self.assertEqual(self.head(),head)
        self.assertEqual(result['next_run'],'2026-11-16T10:00:00+08:00')
        self.assertEqual(list((self.root/'job/runs').iterdir()),[])
    def test_changed_snapshot_pushed_without_runtime_data(self):
        self.change_rule();result=run_job(self.config,self.now)
        self.assertEqual(result['result'],'pushed');self.assertEqual(self.head(),result['commit'])
        files=self.git('--git-dir',str(self.remote),'ls-tree','-r','--name-only','main',cwd=self.root).splitlines()
        self.assertEqual(set(files),{'README.md',SNAPSHOT.as_posix()})
        body=self.git('--git-dir',str(self.remote),'show','main:'+SNAPSHOT.as_posix(),cwd=self.root)
        self.assertNotIn('private-session-marker',body);self.assertNotIn('private-evidence-marker',body)
        self.assertNotIn('PRIVATE-CONVERSATION',body);self.assertEqual(json.loads(body)[0]['text'],self.rule['text'])
    def test_hourly_check_does_not_publish_twice_in_one_week(self):
        run_job(self.config,self.now);self.change_rule();head=self.head()
        self.assertEqual(run_job(self.config,self.now+dt.timedelta(hours=1))['result'],'not_due')
        self.assertEqual(self.head(),head)
        self.assertEqual(run_job(self.config,self.now+dt.timedelta(days=7))['result'],'pushed')
    def test_failed_push_is_retried_without_marking_success(self):
        self.change_rule();hook=self.remote/'hooks/pre-receive';hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o755)
        head=self.head()
        with self.assertRaises(SyncError):run_job(self.config,self.now)
        status=json.loads((self.root/'job/status.json').read_text())
        self.assertEqual(status['result'],'error');self.assertNotIn('last_success_slot',status);self.assertEqual(self.head(),head)
        hook.unlink();self.assertEqual(run_job(self.config,self.now+dt.timedelta(hours=1))['result'],'pushed')
    def test_remote_document_changes_are_preserved(self):
        (self.origin/'README.md').write_text('Updated by a different task\n');self.git('add','README.md');self.git('commit','-m','Update docs');self.git('push','origin','main')
        self.change_rule();run_job(self.config,self.now)
        self.assertEqual(self.git('--git-dir',str(self.remote),'show','main:README.md',cwd=self.root),'Updated by a different task')
    def test_symlink_snapshot_cannot_overwrite_local_database(self):
        target=self.origin/SNAPSHOT;target.unlink();target.symlink_to(self.state/'feedback.sqlite3');self.git('add',SNAPSHOT.as_posix());self.git('commit','-m','Unsafe fixture');self.git('push','origin','main')
        before=(self.state/'feedback.sqlite3').read_bytes();self.change_rule();before=(self.state/'feedback.sqlite3').read_bytes()
        with self.assertRaises(SyncError):run_job(self.config,self.now)
        self.assertEqual((self.state/'feedback.sqlite3').read_bytes(),before)
    def test_sensitive_text_blocks_export_and_push(self):
        self.rule['text']='给 example@example.com 发送资料。';self.save_rule();head=self.head()
        with self.assertRaises(SyncError):run_job(self.config,self.now)
        self.assertEqual(self.head(),head)
        self.assertNotIn('example@example.com',(self.root/'job/status.json').read_text())
    def test_lock_prevents_overlapping_runs(self):
        work=Path(self.config['work_dir']);work.mkdir()
        with (work/'sync.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(run_job(self.config,self.now)['result'],'already_running')
    def test_config_rejects_embedded_tokens_and_other_hosts(self):
        cfg=dict(self.config,repository='https://github.com/example/feedback.git');validate_config(cfg)
        for address in ['https://token@github.com/example/feedback.git','https://other.example/example/feedback.git']:
            with self.assertRaises(SyncError):validate_config(dict(cfg,repository=address))

if __name__=='__main__':unittest.main(verbosity=2)

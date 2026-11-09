#!/usr/bin/env python3
"""Publish only the anonymized rule snapshot once per due week, using a fresh clone."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from zoneinfo import ZoneInfo

from export_rules import read_active_rules

SNAPSHOT=Path('learn-from-corrections/references/seed-rules.json')
DEFAULT_CONFIG=Path.home()/'.local/share/codex-feedback/weekly-sync/config.json'

class SyncError(RuntimeError):
    pass

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def latest_slot(now,config):
    local=now.astimezone(ZoneInfo(config['timezone']))
    candidate=(local-dt.timedelta(days=(local.weekday()-config['weekday'])%7)).replace(hour=config['hour'],minute=config['minute'],second=0,microsecond=0)
    return candidate if candidate<=local else candidate-dt.timedelta(days=7)

def validate_config(config):
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git',config.get('repository','')):
        raise SyncError('repository_must_be_a_github_https_url_without_credentials')
    if config.get('branch')!='main':raise SyncError('unexpected_target_branch')
    if type(config.get('weekday')) is not int or not 0<=config['weekday']<=6:raise SyncError('invalid_weekday')
    if type(config.get('hour')) is not int or not 0<=config['hour']<=23:raise SyncError('invalid_hour')
    if type(config.get('minute')) is not int or not 0<=config['minute']<=59:raise SyncError('invalid_minute')
    ZoneInfo(config['timezone'])
    for key in ['git','gh','state_dir','work_dir']:
        if not Path(config[key]).is_absolute():raise SyncError('configuration_paths_must_be_absolute')

def publish(config,rules,now):
    work=Path(config['work_dir']);runs=work/'runs';runs.mkdir(parents=True,exist_ok=True,mode=0o700)
    environment=dict(os.environ,GIT_TERMINAL_PROMPT='0',GH_PROMPT_DISABLED='1')
    environment.pop('GIT_ASKPASS',None);environment.pop('SSH_ASKPASS',None)
    git=[config['git'],'-c','credential.helper=','-c','credential.helper=!'+shlex.quote(config['gh'])+' auth git-credential']
    def command(arguments,cwd,stage):
        try:r=subprocess.run(git+arguments,cwd=cwd,env=environment,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired:raise SyncError(stage+'_timeout') from None
        if r.returncode:raise SyncError(stage+'_failed_'+str(r.returncode))
        return r.stdout.strip()
    with tempfile.TemporaryDirectory(prefix='run-',dir=runs) as temporary:
        checkout=Path(temporary)/'repo'
        command(['clone','--quiet','--branch',config['branch'],'--single-branch',config['repository'],str(checkout)],work,'clone')
        target=checkout/SNAPSHOT
        if not target.is_file() or target.is_symlink() or not target.resolve().is_relative_to(checkout.resolve()):
            raise SyncError('snapshot_path_missing_or_unsafe')
        body=json.dumps(rules,ensure_ascii=False,indent=2)+'\n'
        if target.read_text()==body:
            command(['push','--dry-run','origin','HEAD:refs/heads/'+config['branch']],checkout,'verify_write_access')
            return {'result':'no_change','active_rules':len(rules),'write_access_verified':True}
        target.write_text(body)
        command(['config','--local','user.name',config['author_name']],checkout,'author_name')
        command(['config','--local','user.email',config['author_email']],checkout,'author_email')
        command(['add','--',SNAPSHOT.as_posix()],checkout,'stage')
        staged=command(['diff','--cached','--name-only'],checkout,'inspect_stage').splitlines()
        if staged!=[SNAPSHOT.as_posix()]:raise SyncError('unexpected_staged_paths')
        command(['diff','--cached','--check'],checkout,'check_diff')
        command(['commit','--quiet','-m','Update anonymized collaboration rules '+now.date().isoformat()],checkout,'commit')
        head=command(['rev-parse','HEAD'],checkout,'head')
        command(['push','origin','HEAD:refs/heads/'+config['branch']],checkout,'push')
        remote=command(['ls-remote','origin','refs/heads/'+config['branch']],checkout,'verify_remote').split()
        if not remote or remote[0]!=head:raise SyncError('remote_commit_not_confirmed')
        return {'result':'pushed','commit':head,'active_rules':len(rules)}

def run_job(config,now=None,force=False):
    now=now or dt.datetime.now(dt.timezone.utc)
    work=Path(config['work_dir']);work.mkdir(parents=True,exist_ok=True,mode=0o700)
    state_path=work/'status.json'
    with (work/'sync.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'result':'already_running'}
        state=json.loads(state_path.read_text()) if state_path.exists() else {}
        slot=latest_slot(now,config);slot_key=slot.isoformat()
        if not force and state.get('last_success_slot','')>=slot_key:return {'result':'not_due','next_run':(slot+dt.timedelta(days=7)).isoformat()}
        state['last_attempt']=now.isoformat()
        try:
            rules=read_active_rules(config['state_dir'])
            result=publish(config,rules,now)
        except (Exception,SystemExit) as exc:
            state.update(result='error',last_error=str(exc) if isinstance(exc,SyncError) else type(exc).__name__,next_retry='next_hourly_check')
            atomic_json(state_path,state)
            raise SyncError(state['last_error']) from None
        state.update(result);state.update(last_success_slot=slot_key,last_success_at=now.isoformat(),next_run=(slot+dt.timedelta(days=7)).isoformat())
        state.pop('last_error',None);state.pop('next_retry',None)
        atomic_json(state_path,state)
        return state

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG);parser.add_argument('--run-now',action='store_true');parser.add_argument('--status',action='store_true');args=parser.parse_args()
    try:
        config=json.loads(args.config.read_text());validate_config(config)
        if args.status:
            path=Path(config['work_dir'])/'status.json'
            print(json.dumps(json.loads(path.read_text()) if path.exists() else {'result':'never_run'},ensure_ascii=False));return 0
        result=run_job(config,force=args.run_now)
        if result['result'] not in ['not_due','already_running']:print(json.dumps(result,ensure_ascii=False),flush=True)
        return 0
    except (Exception,SystemExit) as exc:
        print(json.dumps({'result':'error','error':str(exc) if isinstance(exc,SyncError) else type(exc).__name__},ensure_ascii=False),flush=True);return 1

if __name__=='__main__':raise SystemExit(main())

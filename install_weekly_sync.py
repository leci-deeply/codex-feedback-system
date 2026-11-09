#!/usr/bin/env python3
"""Install the local macOS weekly job without changing other scheduled jobs."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

from weekly_sync import atomic_json,validate_config

ROOT=Path(__file__).resolve().parent
LABEL='local.codex-feedback.weekly-sync'

def build_plist(config,python,runner):
    work=Path(config['work_dir'])
    return {'Label':LABEL,'ProgramArguments':[python,str(runner),'--config',str(work/'config.json')],
            'StartCalendarInterval':{'Weekday':(config['weekday']+1)%7,'Hour':config['hour'],'Minute':config['minute']},
            'StartInterval':3600,'RunAtLoad':True,'ProcessType':'Background','Umask':63,
            'WorkingDirectory':str(work),
            'EnvironmentVariables':{'PATH':str(Path(config['gh']).parent)+':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin','PYTHONDONTWRITEBYTECODE':'1'},
            'StandardOutPath':str(work/'stdout.log'),'StandardErrorPath':str(work/'stderr.log')}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--enable',action='store_true');parser.add_argument('--hour',type=int,default=10);parser.add_argument('--minute',type=int,default=0);args=parser.parse_args()
    if sys.platform!='darwin':raise SystemExit('定时安装器仅用于 macOS。')
    def git_value(key):
        return subprocess.check_output(['git','config','--get',key],cwd=ROOT,text=True).strip()
    work=Path.home()/'.local/share/codex-feedback/weekly-sync'
    config={'repository':git_value('remote.origin.url'),'branch':'main','state_dir':str(Path.home()/'.local/share/codex-feedback'),
            'work_dir':str(work),'git':shutil.which('git'),'gh':shutil.which('gh') or str(Path.home()/'.local/bin/gh'),
            'author_name':git_value('user.name'),'author_email':git_value('user.email'),
            'timezone':'Asia/Shanghai','weekday':0,'hour':args.hour,'minute':args.minute}
    validate_config(config)
    if not Path(config['gh']).is_file():raise SystemExit('GitHub CLI 未安装。')
    work.mkdir(parents=True,exist_ok=True,mode=0o700)
    old_config=work/'config.json'
    if old_config.exists():
        backup=work/'backups'/dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f');backup.mkdir(parents=True,mode=0o700)
        shutil.copy2(old_config,backup/'config.json')
    runner=work/'runner'
    for relative in ['weekly_sync.py','export_rules.py','learn-from-corrections/scripts/feedback.py']:
        target=runner/relative;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copy2(ROOT/relative,target)
    atomic_json(old_config,config)
    python=shutil.which('python3') or sys.executable
    agents=Path.home()/'Library/LaunchAgents';agents.mkdir(parents=True,exist_ok=True)
    path=agents/(LABEL+'.plist')
    if path.exists():
        backup=work/'backups'/dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f');backup.mkdir(parents=True,mode=0o700);shutil.copy2(path,backup/path.name)
    path.write_bytes(plistlib.dumps(build_plist(config,python,runner/'weekly_sync.py')));path.chmod(0o600)
    if args.enable:
        domain='gui/'+str(os.getuid());target=domain+'/'+LABEL
        loaded=subprocess.run(['/bin/launchctl','print',target],capture_output=True)
        if loaded.returncode==0:subprocess.run(['/bin/launchctl','bootout',target],check=True)
        subprocess.run(['/bin/launchctl','bootstrap',domain,str(path)],check=True)
    print(json.dumps({'installed':True,'enabled':args.enable,'label':LABEL,'schedule':'Monday '+f'{args.hour:02d}:{args.minute:02d}'+' Asia/Shanghai','config_file':str(old_config),'launch_agent':str(path)},ensure_ascii=False))

if __name__=='__main__':main()

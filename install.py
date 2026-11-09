#!/usr/bin/env python3
"""Install the prepared skill and merge a single reviewed UserPromptSubmit hook."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PACKAGE=Path(__file__).resolve().parent
NAME='learn-from-corrections'

def main():
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');p.add_argument('--codex-home',type=Path);p.add_argument('--skill-parent',type=Path);p.add_argument('--report',type=Path);p.add_argument('--backup-dir',type=Path);a=p.parse_args()
    codex=a.codex_home or Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    parent=a.skill_parent or codex/'skills';target=parent/NAME
    writable=os.access(parent if parent.exists() else parent.parent,os.W_OK) and os.access(codex,os.W_OK)
    if a.check:
        print(json.dumps({'write_access':writable,'skill_installed':(target/'SKILL.md').exists(),'hook_config_exists':(codex/'hooks.json').exists(),'hook_review_required':True},ensure_ascii=False));return 0
    if not writable:
        print('当前进程没有 Codex 配置目录写权限；尚未安装或修改全局配置。',file=sys.stderr);return 2
    source=PACKAGE/NAME
    if not (source/'scripts/feedback.py').exists(): raise ValueError('技能包不完整')
    hooks_path=codex/'hooks.json'
    original=hooks_path.read_text() if hooks_path.exists() else None
    configuration=json.loads(original) if original else {}
    if not isinstance(configuration,dict): raise ValueError('现有 hooks.json 格式不符合预期，未修改')
    hooks=configuration.setdefault('hooks',{})
    if not isinstance(hooks,dict): raise ValueError('现有 hooks 字段格式不符合预期，未修改')
    command=shlex.quote(sys.executable)+' '+shlex.quote(str(target/'scripts/feedback.py'))+' hook'
    for event_name in ['UserPromptSubmit','Stop']:
        entries=hooks.setdefault(event_name,[])
        if not isinstance(entries,list): raise ValueError('现有钩子事件格式不符合预期，未修改')
        matching=[]
        for entry in entries:
            for handler in entry.get('hooks',[]):
                try: tokens=shlex.split(handler.get('command',''))
                except ValueError: tokens=[]
                if tokens[-2:]==[str(target/'scripts/feedback.py'),'hook']: matching.append(handler)
        if not matching: entries.append({'hooks':[{'type':'command','command':command,'timeout':5,'additionalContextLimit':2000}]})
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup=(a.backup_dir or Path.home()/'.local/share/codex-feedback/installation-backups')/stamp;backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    if original is not None:
        (backup/'hooks.json').write_text(original);os.chmod(backup/'hooks.json',0o600)
    if target.exists(): shutil.copytree(target,backup/NAME)
    parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    temporary=hooks_path.with_name('hooks.json.feedback-tmp')
    temporary.write_text(json.dumps(configuration,ensure_ascii=False,indent=2)+'\n');os.chmod(temporary,0o600);os.replace(temporary,hooks_path)
    env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1'
    subprocess.run([sys.executable,str(target/'scripts/feedback.py'),'status'],check=True,stdout=subprocess.DEVNULL,env=env)
    report={'installed':True,'hook_registered':True,'hook_trusted':False,'live_trigger_verified':False,
            'hook_definition_sha256':hashlib.sha256(command.encode()).hexdigest(),
            'next_step':'在 Codex 中打开 /hooks，审阅并信任此 UserPromptSubmit 钩子。重新打开会话后验证触发。'}
    (a.report or PACKAGE/'installation-status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0

if __name__=='__main__': raise SystemExit(main())

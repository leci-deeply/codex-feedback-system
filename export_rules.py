#!/usr/bin/env python3
"""Export active, anonymized rule text; never export conversations or identifiers."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parent

def read_active_rules(state_dir):
    source=Path(state_dir)/'feedback.sqlite3'
    if not source.is_file():raise SystemExit('本地准则库不存在，未修改快照。')
    spec=importlib.util.spec_from_file_location('feedback',ROOT/'learn-from-corrections/scripts/feedback.py')
    feedback=importlib.util.module_from_spec(spec);spec.loader.exec_module(feedback)
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT payload FROM rules ORDER BY rule_id').fetchall()
    exported=[]
    for (raw,) in rows:
        rule=json.loads(raw)
        if rule.get('status')!='active':continue
        feedback.safe_text(rule['scope']);feedback.safe_text(rule['text'])
        exported.append({'id':rule['id'],'scope':rule['scope'],'text':rule['text'],'status':'active','explicit_long_term':bool(rule.get('explicit_long_term',False)),'support':['curated-baseline'],'evidence':[]})
    if not exported:raise ValueError('没有生效准则，未修改快照。')
    return exported

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--state-dir',type=Path,default=Path(os.environ.get('CODEX_FEEDBACK_HOME',str(Path.home()/'.local/share/codex-feedback'))))
    args=p.parse_args()
    exported=read_active_rules(args.state_dir)
    spec=importlib.util.spec_from_file_location('feedback',ROOT/'learn-from-corrections/scripts/feedback.py')
    feedback=importlib.util.module_from_spec(spec);spec.loader.exec_module(feedback)
    target=ROOT/'learn-from-corrections/references/seed-rules.json'
    feedback.atomic(target,json.dumps(exported,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'exported_active_rules':len(exported),'runtime_identifiers_exported':False,'raw_conversations_exported':False},ensure_ascii=False))

if __name__=='__main__':main()

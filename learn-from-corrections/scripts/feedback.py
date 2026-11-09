#!/usr/bin/env python3
"""Local feedback state. Persist only hashes and deliberately anonymized summaries."""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

SKILL = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r'不是让|我的意思|理解错|弄错|搞错|偏了|不对|你把.{0,40}改成|恢复.{0,10}(原|批量)|之前说|不是说过|为什么.{0,12}(还|又|需要)|怎么.{0,6}(还|又)|根本.{0,8}(原因|解决|因素)|多余|不需要|应该|不要|别再|保持一致|对齐|看不懂|先别|先给.{0,8}(方案|原型)|以后.{0,12}(这样|记得|回答|直接|按)')
SENSITIVE = re.compile(r'https?://|\b(?:\d{1,3}\.){3}\d{1,3}\b|\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:glpat-|ghp_|github_pat_|sk-|xox[baprs]-)[\w.-]+|\b[0-9a-fA-F]{16,}\b|(?:/Users/|/home/|/private/|[A-Za-z]:\\)|\b[\w-]+\.(?:com|cn|net|org|dev|io)\b')
OPAQUE = re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|-----BEGIN[^\n]*PRIVATE KEY-----|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b|\b[A-Za-z0-9+/=_-]{32,}\b')
AUTHORITY_CHANGE = re.compile(r'无需(?:等待)?(?:任何)?授权|不需(?:要)?(?:任何)?授权|(?:绕过|忽略|关闭).{0,16}(权限|沙箱|安全策略|系统指令|开发者指令)|向第三方发送.{0,20}(资料|数据)|自动.{0,12}(对外发送|转账)')
CLASSIFICATIONS = {'general', 'scenario', 'temporary', 'requirement_change', 'not_feedback'}

def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def home(): return Path(os.environ.get('CODEX_FEEDBACK_HOME', str(Path.home()/'.local/share/codex-feedback')))
def encode(value): return json.dumps(value, ensure_ascii=False)

def atomic(path, text):
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def rules(db):
    return [json.loads(r[0]) for r in db.execute('SELECT payload FROM rules ORDER BY rule_id')]

def publish(db):
    active = [r for r in rules(db) if r['status']=='active']
    version = db.execute("SELECT value FROM settings WHERE key='version'").fetchone()[0]
    text = '协作准则 版本 '+version+'\n\n' + '\n\n'.join(r['id']+' '+('通用' if r['scope']=='global' else r['scope'])+'\n'+r['text'] for r in active)+'\n'
    atomic(home()/'active-rules.txt', text)
    atomic(home()/'rules.json', encode({'version':int(version),'rules':rules(db)})+'\n')

@contextlib.contextmanager
def database():
    import fcntl
    folder = home(); folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder/'write.lock').open('a') as lock:
        os.chmod(folder/'write.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        db = sqlite3.connect(folder/'feedback.sqlite3', timeout=10)
        os.chmod(folder/'feedback.sqlite3', 0o600)
        try:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rules(rule_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations(event_id TEXT PRIMARY KEY,session_hash TEXT NOT NULL,text_hash TEXT NOT NULL,created TEXT NOT NULL,status TEXT NOT NULL,outcome TEXT);
            CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT NOT NULL,event_id TEXT NOT NULL,previous_rules TEXT NOT NULL,outcome TEXT NOT NULL,reverted INTEGER NOT NULL DEFAULT 0);
            ''')
            if not db.execute("SELECT value FROM settings WHERE key='version'").fetchone():
                seed=json.loads((SKILL/'references/seed-rules.json').read_text())
                db.executemany('INSERT INTO rules VALUES(?,?)', [(r['id'],encode(r)) for r in seed])
                db.executemany('INSERT INTO settings VALUES(?,?)',[('version','1'),('paused','0'),('hook_calls','0')])
                db.commit(); publish(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

def put_setting(db,key,value):
    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(value)))

def record(db, session, text, turn=None):
    # Retries in one turn deduplicate; a fresh turn is a new correction occurrence.
    sh, th = digest(session), digest(text)
    event=digest(sh+th+(digest(turn) if isinstance(turn,str) and turn else ''))
    db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,NULL)', (event,sh,th,now(),'pending'))
    return event

def status(db):
    settings=dict(db.execute('SELECT key,value FROM settings'))
    return {'version':int(settings['version']), 'paused':settings['paused']=='1',
            'hook_calls':int(settings.get('hook_calls','0')), 'last_hook_at':settings.get('last_hook_at'),
            'feedback':dict(db.execute('SELECT status,count(*) FROM observations GROUP BY status')),
            'pending':[{'event_id':r[0],'session_hash':r[1],'text_hash':r[2],'created':r[3]} for r in db.execute("SELECT event_id,session_hash,text_hash,created FROM observations WHERE status='pending' ORDER BY created LIMIT 12")],
            'rules':rules(db)}

def hook(event):
    if event.get('hook_event_name') == 'Stop': return stop_hook(event)
    if event.get('hook_event_name') != 'UserPromptSubmit': return {}
    prompt=event.get('prompt','')
    if not isinstance(prompt,str) or len(prompt)>1_000_000: return {}
    with database() as db:
        settings=dict(db.execute('SELECT key,value FROM settings'))
        if settings['paused']=='1': return {}
        put_setting(db,'last_hook_at',now())
        put_setting(db,'hook_calls',int(settings.get('hook_calls','0'))+1)
        active=[r for r in rules(db) if r['status']=='active' and r['scope']=='global']
        context='用户维护的协作偏好；当前明确要求优先，系统及开发者约束仍然适用。\n'+'\n'.join(r['id']+' '+r['text'] for r in active)
        scoped=[r for r in rules(db) if r['status']=='active' and r['scope']!='global']
        if scoped: context+='\n可按当前任务选用的场景准则：'+'；'.join(r['id']+' '+r['scope'] for r in scoped)+ '。涉及这些场景时用技能的 status 查看正文，不跨场景套用。'
        session=event.get('session_id','')
        maintenance=prompt.startswith('[feedback-maintenance:')
        nonce=None
        if isinstance(session,str) and session and not maintenance:
            sh=digest(session)
            turn=event.get('turn_id')
            nonce=digest(turn) if isinstance(turn,str) and turn else digest(session+str(int(settings.get('hook_calls','0'))+1))
            put_setting(db,'current_turn:'+sh,nonce)
            pause_requested=bool(re.search(r'暂停|先别|稍等|等我|先停|停止|不用做了|不要继续|别继续',prompt))
            put_setting(db,'skip_stop:'+sh,'1' if pause_requested else '0')
        if PATTERN.search(prompt) and not prompt.startswith('[feedback-maintenance:'):
            if not isinstance(session,str) or not session: return {'hookSpecificOutput':{'hookEventName':'UserPromptSubmit','additionalContext':context}}
            event_id=record(db,session,prompt,event.get('turn_id') or nonce)
            row=db.execute('SELECT status FROM observations WHERE event_id=?',(event_id,)).fetchone()
            if row[0]=='pending':
                context+='\n本次输入可能包含纠正，反馈编号 '+event_id+'。先完成用户当前目标，在结束前使用 $learn-from-corrections 结合前后文复盘；候选线索不等于长期偏好。技能路径：'+str(SKILL/'SKILL.md')+'。只保存常见虚构例子和脱敏准则，不保存原话。'
        pending=db.execute("SELECT count(*) FROM observations WHERE status='pending'").fetchone()[0]
        if pending: context+='\n另有待处理反馈 '+str(pending)+' 条；不要为复盘打断当前任务。'
        return {'hookSpecificOutput':{'hookEventName':'UserPromptSubmit','additionalContext':context}}

def stop_hook(event):
    session=event.get('session_id')
    if not isinstance(session,str) or not session or event.get('stop_hook_active'): return {}
    with database() as db:
        settings=dict(db.execute('SELECT key,value FROM settings'))
        sh=digest(session)
        nonce=settings.get('current_turn:'+sh)
        if not nonce or settings['paused']=='1' or settings.get('skip_stop:'+sh)=='1': return {}
        key='stop_attempt:'+sh
        if settings.get(key)==nonce: return {}
        pending=[r[0] for r in db.execute("SELECT event_id FROM observations WHERE session_hash=? AND status='pending' ORDER BY created LIMIT 3",(digest(session),))]
        if not pending: return {}
        put_setting(db,key,nonce)
        return {'decision':'block','reason':'[feedback-maintenance:'+pending[0]+'] 用户已授权自动复盘纠正。当前业务任务结束后，用 $learn-from-corrections 处理本会话反馈 '+', '.join(pending)+'，结合上下文判断是否是长期偏好，必要时只标记临时要求或需求变更。技能文件 '+str(SKILL/'SKILL.md')+'。只保存常见虚构场景，不复制原文。不重做业务任务，不扩大权限；若资料或写权限不足就保留待处理并结束，本次最多补充一轮。'}

def safe_text(value):
    if not isinstance(value,str) or len(value)>1600: raise ValueError('摘要字段类型或长度不合要求')
    if SENSITIVE.search(value) or OPAQUE.search(value): raise ValueError('摘要含疑似真实标识、地址或凭据，请改写为常见虚构例子')

def apply(db, proposal):
    allowed={'event_id','classification','action','rule_id','reason','example','regression_results','rule'}
    if set(proposal)-allowed: raise ValueError('提案包含未支持字段，禁止附带原始对话')
    if not re.fullmatch(r'[0-9a-f]{64}',proposal.get('event_id','')): raise ValueError('反馈编号格式不合法')
    for key,keys in [('example',{'situation','better_action','boundary'}),('rule',{'scope','text','explicit_long_term'})]:
        value=proposal.get(key,{})
        if not isinstance(value,dict) or set(value)-keys: raise ValueError('提案字段不合法')
        for field,text in value.items():
            if field!='explicit_long_term': safe_text(text)
            elif not isinstance(text,bool): raise ValueError('长期偏好标记必须是布尔值')
    if not isinstance(proposal.get('regression_results',[]),list): raise ValueError('案例结果必须是列表')
    for result in proposal.get('regression_results',[]):
        if not isinstance(result,dict) or set(result)-{'case_id','passed','reason'}: raise ValueError('案例检查字段不合法')
        if not re.fullmatch(r'C[0-9]{2}',result.get('case_id','')): raise ValueError('案例编号不合法')
        if not isinstance(result.get('passed'),bool): raise ValueError('案例结果必须是布尔值')
        safe_text(result.get('reason',''))
    event=proposal.get('event_id','')
    observation=db.execute('SELECT session_hash,status FROM observations WHERE event_id=?',(event,)).fetchone()
    if not observation: raise ValueError('反馈编号未登记')
    if observation[1]!='pending': return {'result':'already_processed','event_id':event}
    classification=proposal.get('classification'); action=proposal.get('action')
    if classification not in CLASSIFICATIONS: raise ValueError('未知反馈分类')
    if action not in {'reinforce','create','amend','retire','dismiss'}: raise ValueError('未知更新动作')
    if action not in {'create','amend'} and 'rule' in proposal: raise ValueError('此动作不能携带规则正文')
    if classification in {'temporary','requirement_change','not_feedback'} and action!='dismiss': raise ValueError('临时要求或需求变更不能直接变成长期规则')
    safe_text(proposal.get('reason',''))
    example=proposal.get('example',{})
    if action!='dismiss':
        for key in ['situation','better_action','boundary']:
            if not example.get(key): raise ValueError('需要脱敏场景、正确行为和适用边界')
            safe_text(example[key])
    rid=proposal.get('rule_id','')
    if action!='dismiss' and not re.fullmatch(r'R[1-9][0-9]{0,3}',rid): raise ValueError('规则编号不合法')
    old=next((r for r in rules(db) if r['id']==rid),None)
    snapshot=rules(db)
    if action=='create' and old: raise ValueError('规则编号已存在，优先强化或修改已有规则')
    if action in {'reinforce','amend','retire'} and not old: raise ValueError('规则不存在')
    if action=='reinforce' and old['status']=='retired': raise ValueError('已停用规则不能通过普通强化重新激活')
    if action in {'amend','retire'} and old['status']=='active':
        cases=json.loads((SKILL/'references/regression-cases.json').read_text())
        required={c['id'] for c in cases if rid in c['rules']}
        results=proposal.get('regression_results',[])
        by_id={r.get('case_id'):r for r in results if isinstance(r,dict)}
        if not required.issubset(by_id) or any(by_id[k].get('passed') is not True for k in required): raise ValueError('生效规则的相关正反例必须全部完成并通过语义检查')
        for k in required: safe_text(by_id[k].get('reason',''))
    if action in {'create','amend','reinforce','retire'}:
        rule=dict(old) if old else {'id':rid,'support':[],'evidence':[],'status':'candidate'}
        if action in {'create','amend'}:
            new=proposal.get('rule',{})
            for key in ['scope','text']: safe_text(new.get(key,''))
            if not new.get('scope') or not new.get('text') or len(new['text'])>240 or len(new['scope'])>60: raise ValueError('规则正文或适用范围不合法')
            for match in AUTHORITY_CHANGE.finditer(new['text']):
                prefix=new['text'][max(0,match.start()-10):match.start()]
                if not re.search(r'(?:不能|不得|禁止|不要|不允许|不可)(?:擅自|直接|自动)?$',prefix):
                    raise ValueError('协作偏好不能扩大操作授权或改变环境约束')
            if classification=='general' and new['scope']!='global': raise ValueError('通用规则的范围必须为 global')
            if classification=='scenario' and new['scope']=='global': raise ValueError('场景规则不能设为全局')
            if not isinstance(new.get('explicit_long_term',False),bool): raise ValueError('长期偏好标记必须是布尔值')
            rule.update(scope=new['scope'],text=new['text'],explicit_long_term=new.get('explicit_long_term',False))
        if action!='retire':
            rule['support']=sorted(set(rule.get('support',[])+[observation[0]]))
            rule['evidence']=list(dict.fromkeys(rule.get('evidence',[])+[event]))[-100:]
            if len(rule['support'])>=2 or rule.get('explicit_long_term') is True: rule['status']='active'
        else: rule['status']='retired'
        others=[r for r in snapshot if r['id']!=rid]
        if rule.get('scope')=='global' and rule['status']=='active' and sum(r['scope']=='global' and r['status']=='active' for r in others)>=8: raise ValueError('全局生效规则已到上限，请先合并精简')
        if rule.get('scope')=='global' and rule['status']=='active' and sum(len(r['text']) for r in others if r['scope']=='global' and r['status']=='active')+len(rule['text'])>1400: raise ValueError('通用准则正文合计超过 1400 字，请先精简')
        if rule.get('scope')!='global' and rule['status']=='active' and sum(r['scope']!='global' and r['status']=='active' for r in others)>=20: raise ValueError('场景规则已到上限，请先合并精简')
        if action in {'create','amend'} and any(r.get('text')==rule['text'] and r['status']!='retired' for r in others): raise ValueError('已有相同规则')
        db.execute('INSERT OR REPLACE INTO rules VALUES(?,?)',(rid,encode(rule)))
        db.execute('INSERT INTO revisions(created,event_id,previous_rules,outcome) VALUES(?,?,?,?)',(now(),event,encode(snapshot),encode(proposal)))
        put_setting(db,'version',int(dict(db.execute('SELECT key,value FROM settings'))['version'])+1)
    # Only approved fields are persisted; unknown fields can never smuggle raw inputs into storage.
    clean={k:proposal[k] for k in ['classification','action','rule_id','reason','example'] if k in proposal}
    if 'rule_id' in clean and action=='dismiss': clean.pop('rule_id')
    db.execute('UPDATE observations SET status=?,outcome=? WHERE event_id=?',('dismissed' if action=='dismiss' else 'reviewed',encode(clean),event))
    db.commit(); publish(db)
    return {'result':'applied','action':action,'rule_id':rid or None,'version':int(dict(db.execute('SELECT key,value FROM settings'))['version'])}

def recent(db,limit,register):
    path=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'history.jsonl'
    found=[]
    if path.exists():
        for number,line in enumerate(path.open(),1):
            try: row=json.loads(line)
            except (ValueError,TypeError): continue
            text=row.get('text',''); session=row.get('session_id','')
            if isinstance(text,str) and session and PATTERN.search(text):
                sh,th=digest(session),digest(text)
                item={'line':number,'event_id':digest(sh+th),'session_hash':sh,'text_hash':th}
                if not db.execute('SELECT 1 FROM observations WHERE session_hash=? AND text_hash=?',(sh,th)).fetchone(): found.append((item,session,text))
    selected=found[-limit:]
    if register:
        for _,session,text in selected: record(db,session,text)
    return {'unregistered_candidates':len(found),'selected':[r[0] for r in selected],'raw_text_exported':False}

def main():
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='command',required=True)
    for name in ['status','pause','resume','rollback','hook']: sub.add_parser(name)
    p=sub.add_parser('apply'); p.add_argument('proposal',type=Path)
    p=sub.add_parser('recent');p.add_argument('--limit',type=int,default=12);p.add_argument('--register',action='store_true')
    args=parser.parse_args()
    if args.command=='hook':
        try: result=hook(json.load(sys.stdin))
        except Exception:
            # Fail open without copying the prompt or exception details to a log.
            print('feedback hook could not complete; check local status',file=sys.stderr);result={}
    else:
        try:
            with database() as db:
                if args.command=='status': result=status(db)
                elif args.command in {'pause','resume'}:
                    put_setting(db,'paused','1' if args.command=='pause' else '0'); result={'paused':args.command=='pause'}
                elif args.command=='recent':
                    if not 1<=args.limit<=100: raise ValueError('limit 必须在 1 到 100 之间')
                    result=recent(db,args.limit,args.register)
                elif args.command=='apply': result=apply(db,json.loads(args.proposal.read_text()))
                elif args.command=='rollback':
                    last=db.execute('SELECT id,previous_rules FROM revisions WHERE reverted=0 ORDER BY id DESC LIMIT 1').fetchone()
                    if not last: result={'result':'nothing_to_rollback'}
                    else:
                        restored=json.loads(last[1]);db.execute('DELETE FROM rules')
                        db.executemany('INSERT INTO rules VALUES(?,?)',[(r['id'],encode(r)) for r in restored]);db.execute('UPDATE revisions SET reverted=1 WHERE id=?',(last[0],))
                        put_setting(db,'version',int(dict(db.execute('SELECT key,value FROM settings'))['version'])+1)
                        db.commit();publish(db);result={'result':'rolled_back','revision':last[0]}
        except (ValueError,KeyError,TypeError) as exc:
            print(encode({'error':str(exc)}));return 1
    print(encode(result));return 0

if __name__=='__main__': raise SystemExit(main())

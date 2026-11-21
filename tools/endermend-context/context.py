#!/usr/bin/env python3
"""Local event journal and deterministic context checkpoints. No network/LLM calls."""
import argparse,datetime,gzip,hashlib,json,os,re,shutil,stat,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
STATE=ROOT/'.context-local'
DOCS=ROOT/'teams/endermend/context'

def redact(text):
    text=re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b','[REDACTED]',text)
    text=re.sub(r'(?i)(authorization[\s"\x27:=]+(?:bearer|token)\s+)[^\s"\x27]+',r'\1[REDACTED]',text)
    text=re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|password|secret|webhook[_-]?token)[\s"\x27:=]+)(?!\$\{|<|\[)[A-Za-z0-9_+/=.-]{12,}',r'\1[REDACTED]',text)
    return text

def git(*args):
    return subprocess.check_output(['git','-c',f'safe.directory={ROOT}',*args],cwd=ROOT,text=True).strip()

def private_dir():
    STATE.mkdir(mode=0o700,exist_ok=True)
    STATE.chmod(0o700)
    if stat.S_IMODE(STATE.stat().st_mode) & 0o077:
        # NTFS mounts may silently ignore chmod. Keep private logs on the home FS.
        target=Path.home()/'.local/state/snowvision-context'/hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():raise RuntimeError('Private archive target already exists; refusing to overwrite')
        shutil.move(str(STATE),str(target))
        target.chmod(0o700)
        for path in target.rglob('*'):path.chmod(0o700 if path.is_dir() else 0o600)
        STATE.symlink_to(target,target_is_directory=True)
        if stat.S_IMODE(target.stat().st_mode) & 0o077:
            raise RuntimeError('Filesystem cannot protect context archives')

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def record(kind,detail):
    private_dir()
    event={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'kind':kind,'head':git('rev-parse','HEAD'),'detail':redact(detail)}
    with (STATE/'events.jsonl').open('a') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
    (STATE/'events.jsonl').chmod(0o600)

def capture():
    private_dir(); head=git('rev-parse','HEAD')
    contents=[]
    for name in ('CURRENT.md','DECISIONS.md','RUNBOOK.md'):
        p=DOCS/name
        if p.exists():contents.append(f'\n## {name}\n'+p.read_text())
    # Bounded handoff; long-term evidence stays in the archive, not in the prompt.
    text=redact('# Context checkpoint\nHEAD: '+head+'\nBranch: '+git('branch','--show-current')+
                '\n\nRecent commits:\n'+git('log','-5','--format=%h %s')+'\n'+''.join(contents))
    if len(text.encode())>16000:raise ValueError('Context exceeds 16 KB: update CURRENT/DECISIONS/RUNBOOK manually')
    payload=text.encode();archive=STATE/'checkpoints'/f'{head}.md.gz'
    write(archive,gzip.compress(payload,mtime=0));write(STATE/'LATEST.md',payload)
    manifest={'head':head,'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload),
              'gzip_bytes':archive.stat().st_size,'archive':str(archive.relative_to(ROOT))}
    write(STATE/'latest.json',(json.dumps(manifest,indent=2)+'\n').encode())
    record('checkpoint',json.dumps(manifest));print('Context checkpoint:',head[:7],len(payload),'bytes')

def verify():
    service=ROOT/'teams/endermend/snowvision'
    commands=[[sys.executable,'-m','unittest','discover','-s',str(ROOT/'tools/endermend-context'),'-v'],
              ['docker','compose','-p','snowvision-context-check','build'],
              ['docker','compose','-p','snowvision-context-check','run','--rm','--no-deps','snowvision','python','-m','unittest','-q']]
    for command in commands:
        record('check-start',' '.join(command))
        result=subprocess.run(command,cwd=service,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        safe=redact(result.stdout);digest=hashlib.sha256(safe.encode()).hexdigest()
        write(STATE/'checks'/f'{digest}.log.gz',gzip.compress(safe.encode(),mtime=0))
        record('check-end',json.dumps({'command':command,'exit_code':result.returncode,'log_sha256':digest}))
        print(safe[-3000:])
        if result.returncode:return result.returncode
    return 0

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['capture','record','verify','commit','archive-chat'])
    parser.add_argument('detail',nargs='?',default='');args=parser.parse_args()
    if args.action=='capture':capture()
    elif args.action=='record':record('note',args.detail)
    elif args.action=='verify':return verify()
    elif args.action=='commit':
        if not args.detail:parser.error('commit requires a message; stage reviewed files first')
        result=verify()
        if result:return result
        return subprocess.call(['git','-c',f'safe.directory={ROOT}','commit','-m',args.detail],cwd=ROOT)
    else:
        source=Path(args.detail).resolve()
        # Explicitly selected chat only; never sweep other conversations or credentials.
        if source.suffix!='.jsonl' or 'sessions' not in source.parts:parser.error('Expected a Codex session JSONL path')
        private_dir();data=redact(source.read_text()).encode()
        digest=hashlib.sha256(data).hexdigest()
        write(STATE/'chat'/f'{digest}.jsonl.gz',gzip.compress(data,mtime=0))
        record('chat-archive',json.dumps({'sha256':digest,'bytes':len(data)}));print('Chat archive saved locally:',digest)
    return 0

if __name__=='__main__':sys.exit(main())

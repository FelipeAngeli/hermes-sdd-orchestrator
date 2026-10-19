#!/usr/bin/env python3
"""Check staged source blobs against the repository token-size policy."""
import argparse, fnmatch, json, os, subprocess, sys
from pathlib import PurePosixPath, Path

EMPTY = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
class Error(Exception): pass
def run(repo, *args, input=None):
    p=subprocess.run(["git","-C",str(repo),*args],input=input,capture_output=True,timeout=30)
    if p.returncode: raise Error((p.stderr or p.stdout).decode(errors="replace").strip() or "git failed")
    return p.stdout
def blob(repo,path): return run(repo,"cat-file","blob",":"+path)
def head_blob(repo,path):
    p=subprocess.run(["git","-C",str(repo),"cat-file","blob","HEAD:"+path],capture_output=True,timeout=30)
    return p.stdout if not p.returncode else None
def load(repo,path):
    try: return json.loads(blob(repo,path))
    except Exception as e: raise Error(f"invalid {path}: {e}")
def valid_path(p): return isinstance(p,str) and not p.startswith("/") and ".." not in PurePosixPath(p).parts and "\\" not in p
def config(repo,path):
    c=load(repo,path); keys={"schema_version","limit_tokens","bytes_per_token","baseline","source_extensions","extensionless_shebang","exclude"}
    if set(c)!=keys or c["schema_version"]!=1 or not all(isinstance(c[x],int) and not isinstance(c[x],bool) and c[x]>0 for x in ("limit_tokens","bytes_per_token")) or not valid_path(c["baseline"]) or not isinstance(c["source_extensions"],list) or not all(isinstance(x,str) for x in c["source_extensions"]) or not isinstance(c["extensionless_shebang"],bool) or not isinstance(c["exclude"],dict) or not all(isinstance(k,str) and isinstance(v,list) and all(isinstance(x,str) for x in v) for k,v in c["exclude"].items()): raise Error("invalid token-limit configuration")
    return c
def baseline(repo,path):
    b=load(repo,path)
    if set(b)!={"schema_version","files"} or b["schema_version"]!=1 or not isinstance(b["files"],dict) or not all(valid_path(k) and isinstance(v,int) and not isinstance(v,bool) and v>=0 for k,v in b["files"].items()): raise Error("invalid token baseline")
    return b["files"]
def policy_integrity(repo,config_path,c,b):
    old_config=head_blob(repo,config_path)
    if old_config is None: return
    if old_config!=blob(repo,config_path): raise Error("token-limit configuration may not change after initial commit")
    previous=head_blob(repo,c["baseline"])
    if previous is None: raise Error("previous token baseline is missing")
    try: old=json.loads(previous)["files"]
    except Exception as e: raise Error(f"invalid previous token baseline: {e}")
    if any(path not in old or size>old[path] for path,size in b.items()): raise Error("token baseline may only shrink or remove entries")
def entries(repo):
    out=run(repo,"ls-files","-s","-z"); ans={}
    for x in out.split(b"\0"):
        if not x: continue
        meta,p=x.split(b"\t",1); mode,oid,stage=meta.split()
        if stage!=b"0": raise Error("unmerged index entry")
        ans[os.fsdecode(p)]=(mode.decode(),oid.decode())
    return ans
def changed(repo):
    probe=subprocess.run(["git","-C",str(repo),"rev-parse","--verify","-q","HEAD^{tree}"],capture_output=True,timeout=30)
    base=probe.stdout.strip().decode() if probe.returncode==0 else EMPTY
    p=subprocess.run(["git","-C",str(repo),"diff-index","--cached","-z","-M","--diff-filter=ACMRTU",base],capture_output=True,timeout=30)
    if p.returncode: raise Error((p.stderr or p.stdout).decode(errors="replace").strip() or "git diff-index failed")
    data=p.stdout.split(b"\0"); ans=[]; i=0
    while i<len(data)-1:
        if not data[i]: i+=1; continue
        head=data[i].decode(errors="surrogateescape"); i+=1; bits=head.split(); status=bits[-1]
        if status[0]=="U": raise Error("unmerged index entry")
        old=os.fsdecode(data[i]); i+=1
        if status[0] in "RC": new=os.fsdecode(data[i]); i+=1; ans.append((new,old))
        else: ans.append((old,None))
    return ans
def display(p): return p if p.isprintable() else repr(p)[1:-1]
def matches(path,pat):
    if pat.startswith("/"): return fnmatch.fnmatchcase(path,pat[1:])
    bits=path.split("/"); return any(fnmatch.fnmatchcase("/".join(bits[i:]),pat) for i in range(len(bits)))
def excluded(path,c): return any(matches(path,p) for v in c["exclude"].values() for p in v)
def source(repo,path,oid,c):
    if excluded(path,c): return False
    ext=PurePosixPath(path).suffix.lower()
    content = run(repo,"cat-file","blob",oid)
    if b"\0" in content: return False
    if ext: return ext in c["source_extensions"]
    return c["extensionless_shebang"] and content[:2]==b"#!"
def selected(repo,mode):
    all_=entries(repo)
    if mode=="all": return [(p,None) for p in all_]
    return changed(repo)
def check(repo,mode,c,b):
    all_=entries(repo); rows=[]; hints=[]; count=0
    for path,old in selected(repo,mode):
        if path not in all_: continue
        m,oid=all_[path]
        if m in ("120000","160000") or not source(repo,path,oid,c): continue
        count+=1
        size=int(run(repo,"cat-file","-s",oid)); allow=b.get(path,b.get(old))
        if allow is not None and size<=allow:
            if size<allow: hints.append((path,allow,size))
        elif size>c["limit_tokens"]*c["bytes_per_token"]:
            rows.append((path,size,allow))
    return rows,hints,count
def report(c,rows,hints,n):
    lim=c["limit_tokens"]; lb=lim*c["bytes_per_token"]
    if rows:
        print(f"token-limit: BLOCKED: {len(rows)} staged source files exceed {lim:,} estimated tokens (tokens = ceil(bytes / {c['bytes_per_token']}); limit {lb:,} bytes):")
        for p,s,a in sorted(rows):
            extra=f" (baseline {a:,} bytes; legacy files may not grow)" if a is not None else f" (limit {lim:,})"
            print(f"  - {display(p)}: {s:,} bytes = {(s+c['bytes_per_token']-1)//c['bytes_per_token']:,} estimated tokens"+extra)
        print("\nHow to fix:\n  1. Identify the separate responsibilities in each file.\n  2. Extract cohesive units into the existing modules where they belong (a new module only when no existing one fits).\n  3. Preserve public contracts: imports, CLI flags, function signatures and outputs stay the same.\n  4. Avoid artificial abstractions: split along real responsibilities, not by line count.\n  5. Run the tests and linters.\n  6. Stage the result (git add) and commit again.\nDo not bypass this check (no --no-verify, no disabling the hook). See AGENTS.md and docs/maintaining-docs.md.")
        return 1
    print(f"token-limit: OK ({n} staged source files checked, limit {lim:,} estimated tokens)")
    for p,a,s in hints: print(f"token-limit: note: {display(p)} shrank below its baseline ({a:,} -> {s:,} bytes). Run python3 tools/check_file_tokens.py --update-baseline and stage tools/token_baseline.json to lock it in.")
    return 0
def update(repo,c,b,admit):
    all_=entries(repo); limit=c["limit_tokens"]*c["bytes_per_token"]; new={}; seen=set(); renamed={}
    missing=[p for p in b if p not in all_]
    for old in missing:
        try: oid=run(repo,"rev-parse",f"HEAD:{old}").strip().decode()
        except Error: continue
        candidates=[p for p,(m,new_oid) in all_.items() if new_oid==oid and p not in b and m not in ("120000","160000") and source(repo,p,new_oid,c)]
        if len(candidates)==1: renamed[candidates[0]]=b[old]
    for p,(m,oid) in all_.items():
        if m in ("120000","160000") or not source(repo,p,oid,c): continue
        s=int(run(repo,"cat-file","-s",oid)); old=b.get(p,renamed.get(p)); allowed=any(matches(p,x) for x in admit)
        if s<=limit: continue
        if old is None and not allowed: print(f"token-limit: {p}: needs --admit"); return 1
        if old is not None and s>old and not allowed: print(f"token-limit: {p}: needs --admit"); return 1
        new[p]=s if old is None or (allowed and s>old) else min(old,s); seen.add(p)
    for p in sorted(set(b)-seen): print(f"token-limit: drop {p}")
    for p in sorted(new):
        if b.get(p)!=new[p]: print(f"token-limit: {'add' if p not in b else ('raise' if new[p]>b[p] else 'lower')} {p}: {b.get(p,'-')} -> {new[p]}")
    Path(repo,c["baseline"]).write_text(json.dumps({"schema_version":1,"files":new},indent=2,sort_keys=True)+"\n")
    print(f"token-limit: wrote {c['baseline']}; git add it to stage the update."); return 0
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--repo",default="."); p.add_argument("--config",default="tools/token_limit.json"); g=p.add_mutually_exclusive_group(required=True); g.add_argument("--staged",action="store_true"); g.add_argument("--all",action="store_true"); g.add_argument("--update-baseline",action="store_true"); p.add_argument("--admit",action="append",default=[]); a=p.parse_args(argv)
    if a.admit and not a.update_baseline: p.error("--admit requires --update-baseline")
    try:
        repo=Path(a.repo).resolve(); c=config(repo,a.config); b=baseline(repo,c["baseline"]); policy_integrity(repo,a.config,c,b)
        if a.update_baseline: return update(repo,c,b,a.admit)
        mode="all" if a.all else "staged"; rows,hints,count=check(repo,mode,c,b); return report(c,rows,hints,count)
    except (Error,OSError,subprocess.TimeoutExpired) as e: print(f"token-limit: ERROR {e}. The commit is blocked (fail closed). Fix the configuration or Git state and retry."); return 2
if __name__=="__main__": raise SystemExit(main())

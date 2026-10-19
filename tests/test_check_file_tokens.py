"""Black-box pre-commit checks for the source token limit."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
class TokenRepo(unittest.TestCase):
 def setUp(self):
  self.d=tempfile.TemporaryDirectory(); self.r=Path(self.d.name)
  (self.r/"tools").mkdir(); (self.r/".githooks").mkdir()
  for p in ("tools/check_file_tokens.py","tools/token_limit.json","tools/token_baseline.json",".githooks/pre-commit"): shutil.copy(ROOT/p,self.r/p)
  (self.r/".githooks/pre-commit").chmod(0o755)
  self.git("init","-q"); self.git("config","user.email","x@y.z"); self.git("config","user.name","x"); self.git("config","core.hooksPath",".githooks")
  self.git("add","."); self.git("commit","-qm","initial")
 def tearDown(self): self.d.cleanup()
 def git(self,*a,ok=True):
  p=subprocess.run(["git","-C",str(self.r),*a],text=True,capture_output=True)
  if ok: self.assertEqual(p.returncode,0,p.stderr+p.stdout)
  return p
 def put(self,p,n):
  q=self.r/p; q.parent.mkdir(parents=True,exist_ok=True); q.write_bytes(b"x"*n); self.git("add",p)
 def cli(self,*args):
  return subprocess.run([sys.executable,"tools/check_file_tokens.py",*args],cwd=self.r,text=True,capture_output=True)
 def test_boundary_and_hook_block(self):
  self.put("src/under.py",19999); self.git("commit","-m","under")
  self.put("src/exact.py",20000); self.git("commit","-m","exact")
  old=self.git("rev-parse","HEAD").stdout; self.put("src/over.py",20001)
  p=self.git("commit","-m","over",ok=False); self.assertEqual(p.returncode,1); self.assertIn("5,001",p.stdout+p.stderr); self.assertEqual(old,self.git("rev-parse","HEAD").stdout)
 def test_exclusions_and_index_only(self):
  self.put("yarn.lock",100000); self.put("api_pb2.py",100000); self.put("note.md",100000); self.git("commit","-m","ignored")
  self.put("src/small.py",10); (self.r/"src/small.py").write_bytes(b"x"*100000); (self.r/"untracked.py").write_bytes(b"x"*100000)
  self.git("commit","-m","index only")
 def test_special_names_and_multiple_sorted(self):
  self.put("src/my file.py",20001); self.put("src/z.py",20001); self.put("src/a\nname.py",20001)
  p=self.git("commit","-m","bad",ok=False); output=p.stdout+p.stderr; self.assertIn("a\\nname.py",output); self.assertLess(output.index("a\\nname.py"),output.index("my file.py"))
 def test_extensionless_shebang_and_deleted(self):
  self.put("script",20001); self.git("commit","-m","plain")
  self.put("run",10); self.git("commit","-m","plain run")
  (self.r/"run").write_bytes(b"#!"+b"x"*19999); self.git("add","run")
  self.assertEqual(self.git("commit","-m","hashbang",ok=False).returncode,1)
  self.git("rm","--cached","run"); self.git("commit","-m","delete")
 def test_partially_staged_uses_index_in_both_directions(self):
  self.put("src/part.py",10); (self.r/"src/part.py").write_bytes(b"x"*100000)
  self.git("commit","-m","small index")
  self.put("src/part.py",100000); (self.r/"src/part.py").write_bytes(b"x"*10)
  self.assertEqual(self.git("commit","-m","large index",ok=False).returncode,1)
 def test_all_checks_unchanged_index_entry_and_check_modes_do_not_mutate(self):
  self.put("src/legacy.py",10); self.git("commit","-m","legacy")
  self.put("src/change.py",10); before=(self.git("diff","--cached","--binary").stdout,self.git("status","--porcelain").stdout,(self.r/"src/change.py").read_bytes())
  for mode in ("--staged","--all"):
   p=self.cli(mode); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
   if mode=="--staged": self.assertIn("1 staged source files checked",p.stdout)
   else: self.assertIn("4 staged source files checked",p.stdout)
  self.assertEqual(before,(self.git("diff","--cached","--binary").stdout,self.git("status","--porcelain").stdout,(self.r/"src/change.py").read_bytes()))
 def test_indexed_config_and_baseline_win_over_unstaged_copies(self):
  self.put("src/no_loophole.py",20001)
  config=json.loads((self.r/"tools/token_limit.json").read_text()); config["limit_tokens"]=99999
  (self.r/"tools/token_limit.json").write_text(json.dumps(config)); self.assertEqual(self.cli("--staged").returncode,1)
  self.git("reset","--hard"); self.put("src/no_baseline_loophole.py",20001)
  (self.r/"tools/token_baseline.json").write_text(json.dumps({"schema_version":1,"files":{"src/no_baseline_loophole.py":20001}}))
  self.assertEqual(self.cli("--staged").returncode,1)
 def test_lock_generated_and_large_source_are_distinct(self):
  self.put("x.lock",100000); self.put("src/generated_pb2.py",100000); self.git("commit","-m","excluded")
  self.put("src/legitimate.py",20001); self.assertEqual(self.git("commit","-m","source",ok=False).returncode,1)
 def test_binary_blob_with_source_extension_is_ignored(self):
  q=self.r/"src/binary.py"; q.parent.mkdir(parents=True,exist_ok=True); q.write_bytes(b"\0"*20001); self.git("add","src/binary.py")
  self.git("commit","-m","binary")
 def test_commit_a_checks_the_automatically_staged_index(self):
  self.put("src/auto.py",10); self.git("commit","-m","initial auto")
  (self.r/"src/auto.py").write_bytes(b"x"*20001)
  self.assertEqual(self.git("commit","-am","automatic",ok=False).returncode,1)

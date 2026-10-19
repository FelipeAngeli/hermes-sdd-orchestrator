"""Baseline ratchet and fail-closed behavior in temporary repositories."""
from tests.test_check_file_tokens import TokenRepo
import json, subprocess

class Baseline(TokenRepo):
 def baseline(self,files):
  (self.r/"tools/token_baseline.json").write_text(json.dumps({"schema_version":1,"files":files})+"\n"); self.git("add","tools/token_baseline.json"); self.git("commit","-m","base")
 def test_baseline_allows_equal_blocks_growth_and_hints_shrink(self):
  self.baseline({"src/old.py":20001})
  self.put("src/old.py",20001); self.git("commit","-m","equal")
  self.put("src/old.py",20002); p=self.git("commit","-m","grow",ok=False); self.assertIn("legacy files may not grow",p.stdout+p.stderr)
  self.put("src/old.py",19000); p=self.git("commit","-m","shrink"); self.assertIn("shrank below",p.stdout+p.stderr)
 def test_update_needs_admit_and_writes_baseline(self):
  self.put("src/new.py",20001)
  p=subprocess.run(["python3","tools/check_file_tokens.py","--update-baseline"],cwd=self.r,text=True,capture_output=True); self.assertEqual(p.returncode,1)
  p=subprocess.run(["python3","tools/check_file_tokens.py","--update-baseline","--admit","*"],cwd=self.r,text=True,capture_output=True); self.assertEqual(p.returncode,0,p.stdout+p.stderr); self.assertIn("src/new.py",(self.r/"tools/token_baseline.json").read_text())
 def test_bad_config_fails_closed(self):
  (self.r/"tools/token_limit.json").write_text("{}") ; self.git("add","tools/token_limit.json")
  p=self.cli("--staged"); self.assertEqual(p.returncode,2); self.assertIn("ERROR",p.stdout)
 def test_malformed_baseline_and_unmerged_index_fail_closed(self):
  (self.r/"tools/token_baseline.json").write_text('{"schema_version":1,"files":{"/bad.py":1}}'); self.git("add","tools/token_baseline.json")
  self.assertEqual(self.cli("--staged").returncode,2)
  self.git("reset","--hard")
  oid=subprocess.run(["git","-C",str(self.r),"hash-object","-w","--stdin"],input=b"x",capture_output=True).stdout.decode().strip()
  p=subprocess.run(["git","-C",str(self.r),"update-index","--index-info"],input=f"100644 {oid} 1\tconflict.py\n",text=True,capture_output=True)
  self.assertEqual(p.returncode,0,p.stderr); self.assertEqual(self.cli("--all").returncode,2)
 def test_rename_carries_allowance_and_update_rekeys_it(self):
  self.baseline({"src/old.py":20001}); self.put("src/old.py",20001); self.git("commit","-m","old")
  self.git("mv","src/old.py","src/new.py")
  p=self.cli("--update-baseline"); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
  files=json.loads((self.r/"tools/token_baseline.json").read_text())["files"]; self.assertEqual(files,{"src/new.py":20001})
  self.git("commit","-m","rename")
 def test_update_lowers_drops_and_refuses_raise_without_admit(self):
  self.baseline({"src/old.py":30000,"src/gone.py":30000,"src/small.py":30000})
  self.put("src/old.py",25000); self.put("src/small.py",10)
  before=(self.r/"tools/token_baseline.json").read_text(); p=self.cli("--update-baseline"); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
  self.assertEqual(json.loads((self.r/"tools/token_baseline.json").read_text())["files"],{"src/old.py":25000})
  self.git("add","tools/token_baseline.json"); self.git("commit","-m","lower")
  self.put("src/old.py",25001); before=(self.r/"tools/token_baseline.json").read_text(); p=self.cli("--update-baseline")
  self.assertEqual(p.returncode,1); self.assertEqual(before,(self.r/"tools/token_baseline.json").read_text())
  p=self.cli("--update-baseline","--admit","*"); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
  self.assertEqual(json.loads((self.r/"tools/token_baseline.json").read_text())["files"],{"src/old.py":25001})

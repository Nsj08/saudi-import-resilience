"""Help/unknown argument checks and whole-revision nonmutation regression."""
import hashlib, subprocess, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SCRIPTS=[ROOT/'manuscript/stage5/build_manuscript.py',ROOT/'manuscript/stage5/formatting/make_assets.py',ROOT/'manuscript/stage5/formatting/render_manuscript.py']
def digest():
    out=[]
    for p in sorted(ROOT.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            out.append((p.relative_to(ROOT).as_posix(),hashlib.sha256(p.read_bytes()).hexdigest()))
    return hashlib.sha256(repr(out).encode()).hexdigest()
class CliSafetyTests(unittest.TestCase):
    def test_help_and_unknown_do_not_mutate(self):
        before=digest()
        for script in SCRIPTS:
            help_run=subprocess.run([sys.executable,str(script),'--help'],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(help_run.returncode,0,(script,help_run.stderr))
            bad=subprocess.run([sys.executable,str(script),'--definitely-unknown'],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(bad.returncode,2,(script,bad.stdout,bad.stderr))
        self.assertEqual(before,digest())
    def test_builder_semantics_check_flag_is_supported(self):
        result=subprocess.run([sys.executable,str(SCRIPTS[0]),'--check'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__': unittest.main()

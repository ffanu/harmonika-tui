"""CAS/rollback tests never invoke systemctl, a network, or a user process."""
import importlib.util,json,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('activation',Path(__file__).resolve().parents[1]/'scripts/level14-activate.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class Activation(unittest.TestCase):
 def test_prior_absence_and_cas_rollback(self):
  with tempfile.TemporaryDirectory() as root:
   r=Path(root);base=r/'base';backup=r/'backup';base.mkdir();backup.mkdir()
   (base/'old').write_text('old');(backup/'old').write_text('old');prior={'old':a.digest(base/'old'),'new':None};a.compare(base,prior)
   (base/'old').write_text('candidate');(base/'new').write_text('candidate-new');candidate={n:a.digest(base/n) for n in prior}
   a.rollback(base,backup,list(prior),candidate,prior);a.compare(base,prior)
 def test_concurrent_operator_change_prevents_all_rollback(self):
  with tempfile.TemporaryDirectory() as root:
   r=Path(root);base=r/'base';backup=r/'backup';base.mkdir();backup.mkdir()
   for n in ('a','b'):(base/n).write_text('candidate');(backup/n).write_text('old')
   candidate={n:a.digest(base/n) for n in ('a','b')};(base/'b').write_text('operator')
   with self.assertRaisesRegex(RuntimeError,'source_compare_failed'):a.rollback(base,backup,['a','b'],candidate,{'a':'prior','b':'prior'})
   self.assertEqual((base/'a').read_text(),'candidate');self.assertEqual((base/'b').read_text(),'operator')
 def test_idle_refuses_pending_approval_or_running_turn(self):
  import sqlite3
  with sqlite3.connect(':memory:') as d:
   d.executescript('CREATE TABLE jobs(state TEXT);CREATE TABLE turns(state TEXT);');a.idle(d)
   for table,state in [('jobs','approval_required'),('jobs','running'),('turns','running')]:
    d.execute('INSERT INTO '+table+' VALUES(?)',(state,))
    with self.assertRaises(RuntimeError):a.idle(d)
    d.execute('DELETE FROM '+table)
 def test_atomic_replace_matches_bytes(self):
  with tempfile.TemporaryDirectory() as root:
   r=Path(root);(r/'source').write_bytes(b'candidate');(r/'target').write_bytes(b'old');a.replace(r/'source',r/'target');self.assertEqual(a.digest(r/'source'),a.digest(r/'target'));self.assertEqual((r/'target').stat().st_mode&0o777,0o600)

if __name__=='__main__':unittest.main()

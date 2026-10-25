import ast,json,os,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from backend.web_builder.common import validate,safe_path
from backend.web_builder.templates import deploy,ops,testing
class Recipes(unittest.TestCase):
 def test_all_28_ids_have_real_different_artifacts_and_no_false_execution(self):
  seen=set()
  for module in (deploy,ops,testing):
   for id,meta in module.SKILLS.items():
    self.assertNotIn(id,seen);seen.add(id);args={'project':'fixture-app'};validate(args,meta['schema']);out=meta['handler'](args)
    self.assertFalse(out['data']['executed']);self.assertTrue(out['writes'])
    for path,content in out['writes']:
     safe_path(path);self.assertIsInstance(content,str)
     if path.endswith('.json'):json.loads(content)
     if path.endswith('.py'):ast.parse(content)
     self.assertNotIn('ufw reset',content);self.assertNotIn('rm -rf /',content)
  self.assertEqual(len(seen),28)
 def test_backup_is_real_sqlite_backup_and_source_remains_intact(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'data').mkdir();source=root/'data/app.sqlite'
   with sqlite3.connect(source) as c:c.execute('CREATE TABLE item(value TEXT)');c.execute('INSERT INTO item VALUES(?)',('PRIVATE_SYNTHETIC_FIXTURE',))
   script=root/'backup.py';script.write_text(ops.BACKUP);run=subprocess.run([sys.executable,str(script)],cwd=root,capture_output=True,text=True);self.assertEqual(run.returncode,0,run.stderr);backup=next((root/'backups').glob('*.sqlite'))
   with sqlite3.connect(backup) as c:self.assertEqual(c.execute('SELECT value FROM item').fetchone()[0],'PRIVATE_SYNTHETIC_FIXTURE')
   self.assertEqual(backup.stat().st_mode&0o777,0o600)
 def test_maintenance_updates_need_review_and_do_not_update_native_tui(self):
  for id in ('maintain.update','maintain.security.patch'):
   v=ops.SKILLS[id]['handler']({'project':'fixture-app'});p=json.loads(next(content for path,content in v['writes'] if path.endswith('.json')));self.assertFalse(p['automatic_merge']);self.assertFalse(p['native_harmonika_update'])
 def test_hostname_injection_rejected_before_config_output(self):
  for host in ('example.com; rm -rf /','x\nserver {','https://evil.example','../etc'):
   with self.assertRaises(ValueError):deploy.emit('deploy.nginx.config',{'project':'fixture-app','domain':host})

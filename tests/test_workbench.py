import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/tools'))
import workbench
import workbench_model as model
import workbench_ui as ui


class WorkbenchTests(unittest.TestCase):
    def test_preferences_preserve_brand_credentials_guards_and_original(self):
        original={'providers':{'harmonika':{'api_key':'SYNTHETIC-SECRET'}},'hooks':{'PreToolUse':[{'name':'guard'}]},
            'permissions':{'allowed_tools':[]},'options':{'auto_lsp':False,'disabled_tools':['lsp_rename'],
                'disabled_skills':['crush-config'],'disable_provider_auto_update':True,
                'tui':{'active_theme':'harmonika-cyber','diff_mode':'unified'}}}
        result=workbench.migrate_options(original)
        for key in ('providers','hooks','permissions'):self.assertEqual(result[key],original[key])
        self.assertEqual(result['options']['tui']['active_theme'],'harmonika-cyber')
        self.assertEqual(result['options']['disabled_tools'],['lsp_rename'])
        self.assertTrue(result['options']['auto_lsp']);self.assertIn('harmonika-hooks',result['options']['disabled_skills'])
        self.assertFalse(original['options']['auto_lsp'])
        self.assertEqual(result,workbench.migrate_options(result))

    def test_native_scope_uses_requested_project(self):
        project,directory,session=workbench.native_scope(['--cwd','/tmp/project','--data-dir','data','-s','abc123'],'/tmp')
        self.assertEqual(directory,'/private/tmp/project/data' if sys.platform=='darwin' else '/tmp/project/data')
        self.assertEqual(session,'abc123')

    def test_read_only_sqlite_never_surfaces_chat_or_titles(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'native.db';c=sqlite3.connect(db)
            c.execute('CREATE TABLE sessions(id TEXT,prompt_tokens INTEGER,completion_tokens INTEGER,cost REAL,title TEXT)')
            c.execute("INSERT INTO sessions VALUES ('fixture',10,5,.1,'PRIVATE TITLE')")
            c.execute('CREATE TABLE messages(role TEXT,session_id TEXT,created_at INTEGER,finished_at INTEGER,parts TEXT)')
            c.execute("INSERT INTO messages VALUES ('user','fixture',0,0,'PRIVATE CHAT OTP PASSWORD')");c.commit();c.close()
            before=db.read_bytes();result=model.native_metadata(d)
            self.assertEqual(result['prompt_tokens'],10);self.assertEqual(result['completion_tokens'],5)
            self.assertNotIn('PRIVATE',json.dumps(result));self.assertEqual(before,db.read_bytes())

    def test_unreported_tokens_and_cost_are_not_fake_zeros(self):
        lines='\n'.join(ui.metrics_lines({'native':{'sessions':1,'cost':None,'prompt_tokens':None}}))
        self.assertIn('belum dilaporkan',lines);self.assertNotIn('$0.0000',lines)

    def test_code_preserves_indentation_hash_and_ignores_escape(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'example.py';p.write_text('def f():\n    # comment\n    return "ok"\x1b[31m\n')
            lines=ui.code_lines(d,p)
            self.assertEqual(lines[1],'    # comment');self.assertNotIn('\x1b','\n'.join(lines))

    def test_browser_skips_private_and_symlink_escapes(self):
        with tempfile.TemporaryDirectory() as d,tempfile.TemporaryDirectory() as other:
            root=Path(d);(root/'source.py').write_text('x=1');(root/'.env').write_text('SECRET=1')
            (root/'node_modules').mkdir();(root/'node_modules/dep.js').write_text('dependency')
            (root/'outside').symlink_to(other);(root/'link.py').symlink_to(root/'source.py')
            self.assertEqual(ui.project_files(root),['source.py'])
            for p in [root/'.env',root/'link.py',Path(other)/'outside.py']:
                with self.assertRaises(ValueError):ui.safe_file(root,p)

    def test_fuzzy_filter(self):
        self.assertTrue(ui.fuzzy('srcauth','src/security/auth.ts'));self.assertFalse(ui.fuzzy('xyz','src/auth.ts'))

    def test_context_requires_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'context.json';p.write_text('{"version":1}');p.chmod(0o600)
            self.assertEqual(model.read_context(p),{'version':1})
            p.chmod(0o644)
            with self.assertRaises(ValueError):model.read_context(p)

    def test_status_unknown_metrics_not_fabricated(self):
        text=model.status_line({'api':{'status':'offline'},'cpu':None,'ram':None})
        self.assertIn('Offline',text);self.assertNotIn('CPU 0',text);self.assertNotIn('GPU 0',text)
        self.assertNotIn('\x1b',model.clean('hello\x1b[31m'))

    def test_isolated_tmux_config_preserves_native_keys_and_has_real_panels(self):
        text=workbench.tmux_configuration('/tmp/synthetic/context.json')
        for key in ('F1','F2','F3','F4','F5','F7','F8','F11'):self.assertIn('bind-key -n '+key,text)
        self.assertNotIn('bind-key -n C-p',text);self.assertNotIn('kill-server',text);self.assertNotIn('kill-session',text)

    def test_sparkline_is_bounded(self):
        self.assertEqual(len(model.sparkline(list(range(100)))),24);self.assertEqual(model.sparkline([]),'—')

    def test_launcher_routes_only_interactive_tui_to_workbench(self):
        source=(ROOT/'runtime/harmonika.py').read_text()
        self.assertIn("'--no-workbench'",source)
        self.assertIn('workbench_enabled and not cli_only and sys.stdin.isatty() and sys.stdout.isatty()',source)
        compile(source,'<launcher>','exec')

    def test_no_binary_fails_before_exec(self):
        with self.assertRaises(ValueError):workbench.start(None,[],'/tmp','/tmp')

    def test_cache_sweep_checks_only_private_owned_server_and_never_stops_it(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'instance-old';p.mkdir();ctx=p/'context.json'
            ctx.write_text(json.dumps({'version':1,'socket':'harmonika-workbench-123-fixture'}));ctx.chmod(0o600)
            os.utime(p,(1,1))
            result=type('Result',(),{'returncode':0})()
            with patch.object(workbench.subprocess,'run',return_value=result) as call:
                workbench.sweep_cache(root,'/fixture/tmux');self.assertTrue(p.exists())
                self.assertIn('has-session',call.call_args[0][0]);self.assertNotIn('kill-session',call.call_args[0][0])
            result.returncode=1
            with patch.object(workbench.subprocess,'run',return_value=result):workbench.sweep_cache(root,'/fixture/tmux')
            self.assertFalse(p.exists())


if __name__=='__main__':unittest.main()

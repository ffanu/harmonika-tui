import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

spec = importlib.util.spec_from_file_location('maintenance', Path(__file__).resolve().parents[1] / 'scripts/sqlite-maintenance.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def run_cli(*argv):
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = m.main([str(a) for a in argv])
    return code, json.loads(stream.getvalue())


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.dir = self.root / 'data'
        self.dir.mkdir(mode=0o700)
        self.db = self.dir / 'engine.sqlite3'
        self.make_delete_db(self.db)

    def make_delete_db(self, path, rows=3):
        with contextlib.closing(sqlite3.connect(str(path))) as c,c:
            c.execute('CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, value TEXT)')
            c.executemany('INSERT INTO items(value) VALUES (?)', [('v%d' % i,) for i in range(rows)])
        os.chmod(str(path), 0o600)

    def make_wal_db(self, path, rows=3, keep_open=False):
        conn = sqlite3.connect(str(path))
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, value TEXT)')
        conn.executemany('INSERT INTO items(value) VALUES (?)', [('v%d' % i,) for i in range(rows)])
        conn.commit()
        os.chmod(str(path), 0o600)
        if keep_open:
            return conn
        conn.close()
        return None

    def count(self, path):
        with contextlib.closing(sqlite3.connect(str(path))) as c,c:
            return c.execute('SELECT count(*) FROM items').fetchone()[0]


class Inspect(Base):
    def test_delete_and_wal_header_mode(self):
        code, out = run_cli('inspect', self.db)
        self.assertEqual(code, 0)
        self.assertEqual(out['header_mode'], 'delete')
        self.assertEqual(out['read_probe_journal_mode'], 'delete')
        self.assertEqual(out['read_probe_quick_check'], 'ok')

    def test_writer_open_readonly_reader_succeeds_with_shm(self):
        writer = self.make_wal_db(self.db, keep_open=True)
        self.addCleanup(writer.close)
        self.assertTrue(Path(str(self.db) + '-wal').stat().st_size > 0)
        code, out = run_cli('inspect', self.db)
        self.assertEqual(code, 0)
        self.assertEqual(out['header_mode'], 'wal')
        self.assertEqual(out['read_probe_journal_mode'], 'wal')
        self.assertEqual(out['read_probe_quick_check'], 'ok')

    def test_sidecar_missing_wal_inspection_never_writes_sidecars(self):
        self.make_wal_db(self.db)
        Path(str(self.db) + '-wal').unlink(missing_ok=True)
        Path(str(self.db) + '-shm').unlink(missing_ok=True)
        code, out = run_cli('inspect', self.db)
        self.assertEqual(code, 0)
        self.assertEqual(out['header_mode'], 'wal')
        self.assertEqual(out['read_probe_journal_mode'], 'requires_wal_index')
        self.assertFalse(Path(str(self.db) + '-wal').exists())
        # SQLite owns SHM lifecycle; maintenance must never unlink it manually.

    def test_inspect_missing_and_symlink(self):
        code, out = run_cli('inspect', self.root / 'absent.sqlite3')
        self.assertEqual(code, 0)
        self.assertFalse(out['exists'])
        link = self.root / 'link.sqlite3'
        link.symlink_to(self.db)
        code, out = run_cli('inspect', link)
        self.assertEqual(code, 2)
        self.assertFalse(out['ok'])


class MigrateRevert(Base):
    def test_roundtrip_preserves_data_and_permissions(self):
        code, out = run_cli('migrate-wal', self.db, '--busy-timeout-ms', '500')
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'migrated')
        self.assertEqual(out['before'], 'delete')
        self.assertEqual(out['after'], 'wal')
        self.assertEqual(oct(stat.S_IMODE(os.stat(str(self.db)).st_mode)), '0o600')
        self.assertEqual(oct(stat.S_IMODE(os.stat(str(self.dir)).st_mode)), '0o700')
        backup = Path(out['backup']['backup_path'])
        self.assertTrue(backup.is_file())
        self.assertEqual(oct(stat.S_IMODE(backup.stat().st_mode)), '0o600')
        self.assertEqual(m.read_header(self.db)['header_mode'], 'wal')
        self.assertEqual(self.count(self.db), 3)

        code, out = run_cli('revert-delete', self.db, '--busy-timeout-ms', '500')
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'reverted')
        self.assertEqual(out['after'], 'delete')
        self.assertFalse(out['sidecars_remaining'].get('wal',False))
        self.assertEqual(m.read_header(self.db)['header_mode'], 'delete')
        self.assertFalse(Path(str(self.db) + '-wal').exists())
        # SQLite owns SHM lifecycle; maintenance must never unlink it manually.
        self.assertEqual(self.count(self.db), 3)

    def test_idempotent_both_directions(self):
        code, out = run_cli('migrate-wal', self.db)
        self.assertEqual(out['status'], 'migrated')
        code, out = run_cli('migrate-wal', self.db)
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'already_wal')
        code, out = run_cli('revert-delete', self.db)
        self.assertEqual(out['status'], 'reverted')
        code, out = run_cli('revert-delete', self.db)
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'already_delete')

    def test_backup_snapshot_includes_uncheckpointed_wal_commit(self):
        writer = self.make_wal_db(self.db, rows=3, keep_open=True)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('INSERT INTO items(value) VALUES (?)', ('wal-only',))
        writer.commit()
        baseline = self.count(self.db)
        self.assertTrue(Path(str(self.db) + '-wal').stat().st_size > 0)
        code, out = run_cli('backup', self.db, '--busy-timeout-ms', '500')
        self.assertEqual(code, 0)
        snapshot = Path(out['backup_path'])
        self.assertEqual(self.count(snapshot), baseline)
        with sqlite3.connect(str(snapshot)) as c:
            self.assertEqual(c.execute('PRAGMA quick_check(1)').fetchone()[0], 'ok')
            self.assertIn('wal-only', [r[0] for r in c.execute('SELECT value FROM items')])

    def test_revert_backup_captures_wal_state_before_mode_change(self):
        code, out = run_cli('migrate-wal', self.db)
        self.assertEqual(code, 0)
        writer = sqlite3.connect(str(self.db))
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('INSERT INTO items(value) VALUES (?)', ('pending-in-wal',))
        writer.commit()
        writer.close()
        code, out = run_cli('revert-delete', self.db, '--busy-timeout-ms', '500')
        self.assertEqual(code, 0)
        snapshot = Path(out['backup']['backup_path'])
        self.assertEqual(self.count(snapshot), 4)


class Refusals(Base):
    def test_symlink_database_rejected(self):
        link = self.root / 'linked.sqlite3'
        link.symlink_to(self.db)
        code, out = run_cli('migrate-wal', link)
        self.assertEqual(code, 2)
        self.assertEqual(out['error'], 'unsafe_database_path')
        self.assertEqual(m.read_header(self.db)['header_mode'], 'delete')

    def test_symlink_directory_rejected(self):
        real_dir = self.root / 'real'
        real_dir.mkdir(mode=0o700)
        target = real_dir / 'engine.sqlite3'
        self.make_delete_db(target)
        link_dir = self.root / 'linkdir'
        link_dir.symlink_to(real_dir)
        code, out = run_cli('migrate-wal', link_dir / 'engine.sqlite3')
        self.assertEqual(code, 2)
        self.assertEqual(out['error'], 'unsafe_database_path')

    def test_permission_violation_rejected_then_fixed(self):
        os.chmod(str(self.db), 0o644)
        code, out = run_cli('migrate-wal', self.db)
        self.assertEqual(code, 2)
        self.assertEqual(out['error'], 'unsafe_database_permissions')
        os.chmod(str(self.db), 0o600)
        os.chmod(str(self.dir), 0o755)
        code, out = run_cli('migrate-wal', self.db)
        self.assertEqual(code, 2)
        self.assertEqual(out['error'], 'unsafe_directory_permissions')
        code, out = run_cli('migrate-wal', self.db, '--fix-permissions')
        self.assertEqual(code, 0)
        self.assertEqual(oct(stat.S_IMODE(os.stat(str(self.dir)).st_mode)), '0o700')
        self.assertEqual(oct(stat.S_IMODE(os.stat(str(self.db)).st_mode)), '0o600')
        self.assertEqual(m.read_header(self.db)['header_mode'], 'wal')

    def test_world_writable_refused_even_with_fix(self):
        os.chmod(str(self.db), 0o666)
        code, out = run_cli('migrate-wal', self.db, '--fix-permissions')
        self.assertEqual(code, 2)
        self.assertEqual(out['error'], 'unsafe_database_permissions')

    def test_live_user_database_guard(self):
        live_dir = self.root / '.config/harmonika/state'
        live_dir.mkdir(parents=True, mode=0o700)
        live = live_dir / 'session-cache.sqlite3'
        self.make_delete_db(live)
        code, out = run_cli('migrate-wal', live)
        self.assertEqual(code, 2)
        self.assertEqual(out['error'], 'live_user_database_refused')
        self.assertEqual(m.read_header(live)['header_mode'], 'delete')
        code, out = run_cli('migrate-wal', live, '--allow-live-user-db', '--busy-timeout-ms', '500')
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'migrated')

    def test_hot_rollback_journal_refused(self):
        journal=Path(str(self.db) + '-journal');journal.write_bytes(b'');journal.chmod(0o600)
        code, out = run_cli('migrate-wal', self.db)
        self.assertEqual(code, 2)
        self.assertEqual(out['error'], 'hot_journal_present')

    def test_identity_swap_detected(self):
        identity = m.lstat_info(self.db)['identity']
        swapped = self.root / 'swapped.sqlite3'
        self.make_delete_db(swapped)
        os.unlink(self.db)
        os.rename(swapped, self.db)
        with self.assertRaises(m.MaintError) as ctx:
            m.assert_identity(self.db, identity)
        self.assertEqual(ctx.exception.code, 'identity_changed')


class Concurrency(Base):
    def test_busy_reader_blocks_migration_without_mode_change(self):
        def hold():
            conn = sqlite3.connect(str(self.db), timeout=0.5)
            conn.execute('BEGIN')
            conn.execute('SELECT count(*) FROM items').fetchone()
            time.sleep(1.2)
            conn.rollback()
            conn.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold)
            time.sleep(0.2)
            started = time.monotonic()
            code, out = run_cli('migrate-wal', self.db, '--busy-timeout-ms', '250', '--quiesce-retries', '2')
            elapsed = time.monotonic() - started
            future.result()
        self.assertEqual(code, 3)
        self.assertEqual(out['error'], 'database_busy')
        self.assertLess(elapsed, 10)
        self.assertEqual(m.read_header(self.db)['header_mode'], 'delete')

    def test_active_writer_journal_refused_and_mode_unchanged(self):
        writer = sqlite3.connect(str(self.db), timeout=0.5)
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('INSERT INTO items(value) VALUES (?)', ('active',))
        self.assertTrue(Path(str(self.db) + '-journal').exists())
        code, out = run_cli('migrate-wal', self.db, '--busy-timeout-ms', '250')
        writer.rollback()
        writer.close()
        self.assertEqual(code, 2)
        self.assertEqual(out['error'], 'hot_journal_present')
        self.assertEqual(m.read_header(self.db)['header_mode'], 'delete')

    def test_open_reader_blocks_revert_with_busy(self):
        code, out = run_cli('migrate-wal', self.db)
        self.assertEqual(code, 0)
        reader = sqlite3.connect(str(self.db))
        reader.execute('BEGIN')
        reader.execute('SELECT count(*) FROM items').fetchone()
        self.addCleanup(reader.close)
        code, out = run_cli('revert-delete', self.db, '--busy-timeout-ms', '250')
        self.assertEqual(code, 3)
        self.assertIn(out['error'], ('checkpoint_busy', 'database_busy'))
        self.assertEqual(m.read_header(self.db)['header_mode'], 'wal')
        reader.rollback()
        reader.close()
        code, out = run_cli('revert-delete', self.db, '--busy-timeout-ms', '500')
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'reverted')

    def test_checkpoint_truncate_reclaims_wal(self):
        code, out = run_cli('migrate-wal', self.db)
        self.assertEqual(code, 0)
        writer = sqlite3.connect(str(self.db))
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('INSERT INTO items(value) VALUES (?)', ('frame',))
        writer.commit()
        self.assertTrue(Path(str(self.db) + '-wal').stat().st_size > 0)
        code, out = run_cli('checkpoint', self.db, '--busy-timeout-ms', '500')
        writer.close()
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'checkpointed')
        wal=Path(str(self.db)+'-wal');self.assertTrue(not wal.exists() or wal.stat().st_size==0)
        plain = self.root / 'plain.sqlite3'
        self.make_delete_db(plain)
        code, out = run_cli('checkpoint', plain)
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'not_wal_noop')


class Watchdog(Base):
    def test_inspect_bounded_no_hang_on_locked_delete_db(self):
        def hold():
            conn = sqlite3.connect(str(self.db), timeout=0.5)
            conn.execute('BEGIN EXCLUSIVE')
            time.sleep(1.0)
            conn.rollback()
            conn.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold)
            time.sleep(0.2)
            started = time.monotonic()
            code, out = run_cli('inspect', self.db)
            elapsed = time.monotonic() - started
            future.result()
        self.assertEqual(code, 0)
        self.assertIn(out['read_probe_journal_mode'], ('delete', 'unavailable'))
        self.assertLess(elapsed, 10)


if __name__ == '__main__':
    unittest.main()

class RootHardening(Base):
    def test_symlink_sidecar_is_refused(self):
        target=self.root/'unrelated';target.write_bytes(b'safe');target.chmod(0o600)
        Path(str(self.db)+'-wal').symlink_to(target)
        code,result=run_cli('migrate-wal',self.db)
        self.assertEqual(code,2);self.assertEqual(result['error'],'unsafe_sidecar');self.assertEqual(target.read_bytes(),b'safe')
    def test_backup_has_real_wall_clock_deadline_even_on_exclusive_writer(self):
        writer=sqlite3.connect(self.db);writer.execute('BEGIN EXCLUSIVE')
        old=m.WATCHDOG_SECONDS;m.WATCHDOG_SECONDS=.05;start=time.monotonic()
        try:
            with self.assertRaises(m.MaintError) as caught:m.online_backup(self.db,m.lstat_info(self.db)['identity'],self.root/'backup',20)
            self.assertEqual(caught.exception.code,'backup_timeout');self.assertLess(time.monotonic()-start,1.5)
        finally:m.WATCHDOG_SECONDS=old;writer.rollback();writer.close()
    def test_diagnostic_copy_contains_main_database_not_an_empty_new_file(self):
        with contextlib.closing(sqlite3.connect(self.db)) as c:pages=c.execute('PRAGMA page_count').fetchone()[0]
        result=m.diagnostic_copy(self.db)
        self.assertEqual(result['quick_check'],'ok');self.assertEqual(result['pages'],pages);self.assertFalse(result['authoritative'])

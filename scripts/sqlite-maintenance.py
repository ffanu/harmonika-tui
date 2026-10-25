"""Controlled SQLite journal-mode maintenance: inspect, online backup, migrate DELETE->WAL, revert, checkpoint.

Operator tool for Harmonika-managed databases. Never reads or prints table rows,
chat content or credentials. Refuses live user session stores unless explicitly
allowed, refuses symlinks, enforces 0700/0600, verifies file identity (dev,ino)
across every step, and bounds every connection with busy_timeout plus a progress
watchdog so it cannot hang.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

MAGIC = b'SQLite format 3\x00'
DIR_MODE = 0o700
FILE_MODE = 0o600
LIVE_USER_MARKERS = ('/.config/harmonika/', '/.harmonika/')
LIVE_USER_NAMES = ('session-cache.sqlite3', 'sessions.db')
WATCHDOG_SECONDS = 10.0
BUSY_WORDS = ('locked', 'busy')


class MaintError(Exception):
    def __init__(self, code, exit_code=2):
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


def emit(result, ok=True):
    print(json.dumps({'ok': ok, **result}, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 2


def fail(error):
    print(json.dumps({'ok': False, 'error': error.code}, ensure_ascii=False))
    return error.exit_code


def is_busy_error(exc):
    text = str(exc).lower()
    return any(word in text for word in BUSY_WORDS)


def map_sqlite_error(exc):
    if isinstance(exc, sqlite3.OperationalError) and is_busy_error(exc):
        return MaintError('database_busy', 3)
    return MaintError('database_error', 4)


def system_alias(path):
    resolved = str(Path(str(path)).resolve())
    return resolved == '/private' + str(path)


def path_chain_symlink(path):
    absolute = Path(path).absolute()
    if absolute.is_symlink():
        return True
    for parent in absolute.parents:
        if parent.is_symlink() and not system_alias(parent):
            return True
    return False


def lstat_info(path):
    info = os.lstat(str(path))
    return {
        'exists': True,
        'mode': '0o%04o' % (stat.S_IMODE(info.st_mode)),
        'size_bytes': info.st_size,
        'nlink': info.st_nlink,
        'uid': info.st_uid,
        'symlink': stat.S_ISLNK(info.st_mode),
        'regular': stat.S_ISREG(info.st_mode),
        'identity': (info.st_dev, info.st_ino),
    }


def read_header(path):
    with open(str(path), 'rb') as stream:
        raw = stream.read(100)
    if len(raw) < 100 or raw[:16] != MAGIC:
        return {'magic_ok': False, 'header_mode': 'not_sqlite'}
    write_version, read_version = raw[18], raw[19]
    if write_version == 2 and read_version == 2:
        mode = 'wal'
    elif write_version == 1 and read_version == 1:
        mode = 'delete'
    else:
        mode = 'unknown'
    page_size = int.from_bytes(raw[16:18], 'big')
    return {
        'magic_ok': True,
        'header_mode': mode,
        'write_version': write_version,
        'read_version': read_version,
        'page_size': 65536 if page_size == 1 else page_size,
        'change_counter': int.from_bytes(raw[24:28], 'big'),
    }


def sidecars(path):
    out = {}
    for label, suffix in (('wal', '-wal'), ('shm', '-shm'), ('journal', '-journal')):
        item = Path(str(path) + suffix)
        if item.is_symlink():
            out[label] = {'present': True, 'symlink': True, 'size_bytes': 0, 'mode': None}
        elif item.exists():
            info = lstat_info(item)
            out[label] = {'present': True, 'symlink': False, 'size_bytes': info['size_bytes'], 'mode': info['mode']}
        else:
            out[label] = {'present': False, 'symlink': False, 'size_bytes': 0, 'mode': None}
    return out


def preflight(path, fix_permissions=False, allow_live_user_db=False):
    path = Path(path).absolute()
    if path_chain_symlink(path):
        raise MaintError('unsafe_database_path')
    if not path.exists():
        raise MaintError('database_not_found')
    database = lstat_info(path)
    if not database['regular'] or database['symlink']:
        raise MaintError('unsafe_database_path')
    if database['nlink'] != 1 or database['uid'] != os.getuid():
        raise MaintError('unsafe_database_identity')
    rendered = str(path)
    looks_live = any(marker in rendered for marker in LIVE_USER_MARKERS) or path.name in LIVE_USER_NAMES
    if looks_live and not allow_live_user_db:
        raise MaintError('live_user_database_refused')
    directory = path.parent
    dir_stat = os.stat(str(directory))
    if dir_stat.st_uid != os.getuid():
        raise MaintError('unsafe_directory_owner')
    if stat.S_IMODE(dir_stat.st_mode) != DIR_MODE:
        if fix_permissions and not stat.S_IMODE(dir_stat.st_mode) & 0o022:
            os.chmod(str(directory), DIR_MODE)
        else:
            raise MaintError('unsafe_directory_permissions')
    file_mode = stat.S_IMODE(os.stat(str(path)).st_mode)
    if file_mode != FILE_MODE:
        if fix_permissions and not file_mode & 0o022:
            os.chmod(str(path), FILE_MODE)
        else:
            raise MaintError('unsafe_database_permissions')
    for suffix in ('-wal','-shm','-journal'):
        side=Path(str(path)+suffix)
        if side.exists() or side.is_symlink():
            info=lstat_info(side)
            if info['symlink'] or not info['regular'] or info['uid']!=os.getuid() or info['nlink']!=1 or info['mode']!='0o0600':
                raise MaintError('unsafe_sidecar')
    header = read_header(path)
    if not header['magic_ok']:
        raise MaintError('not_sqlite_database')
    return database, header


def assert_identity(path, identity, code='identity_changed'):
    current = lstat_info(path)
    if current['identity'] != identity:
        raise MaintError(code)


def connect(path, busy_ms, watchdog=WATCHDOG_SECONDS):
    connection = sqlite3.connect(str(path), timeout=busy_ms / 1000.0)
    deadline = time.monotonic() + watchdog
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
    connection.execute('PRAGMA busy_timeout=%d' % int(busy_ms))
    return connection


def open_rw_checked(path, identity, busy_ms):
    assert_identity(path, identity)
    try:
        return connect(path, busy_ms)
    except sqlite3.Error as exc:
        raise map_sqlite_error(exc) from None


def quiesce_probe(connection, retries, delay=0.3):
    for attempt in range(max(1, retries)):
        try:
            connection.execute('BEGIN EXCLUSIVE')
            connection.rollback()
            return
        except sqlite3.Error as exc:
            if is_busy_error(exc) and attempt + 1 < max(1, retries):
                time.sleep(delay)
                continue
            if is_busy_error(exc):
                raise MaintError('database_busy', 3) from None
            raise map_sqlite_error(exc) from None


def quick_check(connection):
    return connection.execute('PRAGMA quick_check(1)').fetchone()[0]


def enforce_permissions(path):
    os.chmod(str(path), FILE_MODE)
    for suffix in ('-wal', '-shm'):
        item = Path(str(path) + suffix)
        if item.exists() and not item.is_symlink():
            os.chmod(str(item), FILE_MODE)
    os.chmod(str(path.parent), DIR_MODE)


def online_backup(path, identity, backup_dir, busy_ms):
    backup_dir = Path(backup_dir).absolute()
    if path_chain_symlink(backup_dir):
        raise MaintError('unsafe_backup_path')
    backup_dir.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    os.chmod(str(backup_dir), DIR_MODE)
    target = backup_dir / ('%s.maintenance-%d-%s.sqlite3' % (path.name, int(time.time()), os.urandom(4).hex()))
    assert_identity(path, identity)
    try:
        with closing(connect(path, busy_ms)) as source, closing(connect(target, busy_ms)) as destination:
            deadline = time.monotonic() + WATCHDOG_SECONDS
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise MaintError('backup_timeout', 3)
            source.backup(destination, pages=128, progress=progress, sleep=.05)
            check = quick_check(destination)
            pages = destination.execute('PRAGMA page_count').fetchone()[0]
    except MaintError:
        target.unlink(missing_ok=True)
        raise
    except sqlite3.Error as exc:
        target.unlink(missing_ok=True)
        raise map_sqlite_error(exc) from None
    if check != 'ok':
        target.unlink(missing_ok=True)
        raise MaintError('backup_integrity_failed', 4)
    os.chmod(str(target), FILE_MODE)
    assert_identity(path, identity)
    return {'backup_path': str(target), 'backup_pages': pages, 'backup_quick_check': check}


def checkpoint_truncate(connection):
    busy, log_pages, checkpointed = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    if busy:
        raise MaintError('checkpoint_busy', 3)
    return {'wal_frames': log_pages, 'checkpointed_frames': checkpointed}


def set_journal_mode(connection, mode):
    current = connection.execute('PRAGMA journal_mode=%s' % mode).fetchone()[0]
    if current.lower() != mode:
        raise MaintError('mode_change_refused', 3)
    return current


def default_backup_dir(path):
    return Path(str(path) + '.maintenance-backups')


def cmd_inspect(args):
    path = Path(args.database).absolute()
    result = {'database': str(path)}
    if path_chain_symlink(path):
        return emit({'error_detail': 'symlink_in_path_chain', **result}, ok=False)
    if not path.exists():
        return emit({'exists': False, **result})
    database = lstat_info(path)
    result.update({'exists': True, 'mode': database['mode'], 'size_bytes': database['size_bytes'],
                   'nlink': database['nlink'], 'symlink': database['symlink'], 'sidecars': sidecars(path)})
    header = read_header(path)
    result.update(header)
    probe = 'skipped'
    probe_check = None
    missing_wal_index = header.get('header_mode') == 'wal' and not Path(str(path)+'-shm').exists()
    if missing_wal_index:
        # mode=ro may create WAL/SHM on SQLite/Linux. Header inspection remains
        # strictly non-mutating: request an explicit private copy for diagnosis.
        probe='requires_wal_index'
    elif header.get('magic_ok') and database['regular'] and not database['symlink'] and database['nlink'] == 1:
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1.0)) as connection:
                connection.execute('PRAGMA busy_timeout=1000')
                deadline = time.monotonic() + 3.0
                connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
                probe = connection.execute('PRAGMA journal_mode').fetchone()[0]
                probe_check = quick_check(connection)
        except sqlite3.Error:
            probe = 'unavailable'
            probe_check = None
    result['read_probe_journal_mode'] = probe
    result['read_probe_quick_check'] = probe_check
    if args.copy_diagnostic and probe in ('unavailable','requires_wal_index') and header.get('magic_ok'):
        result['diagnostic_copy'] = diagnostic_copy(path)
    return emit(result)


def diagnostic_copy(path):
    import tempfile
    with tempfile.TemporaryDirectory(prefix='harmonika-sql-inspect-') as temp:
        copy = Path(temp) / path.name
        copied = [copy]
        if path.stat().st_size > 64*1024*1024:
            return {'authoritative':False,'original_modified':False,'error':'diagnostic_copy_limit'}
        import shutil
        shutil.copyfile(path,copy)
        os.chmod(copy,0o600)
        for suffix in ('-wal',):
            source = Path(str(path) + suffix)
            if source.exists() and not source.is_symlink():
                if source.stat().st_size > 64*1024*1024:
                    return {'authoritative':False,'original_modified':False,'error':'diagnostic_copy_limit'}
                destination = Path(str(copy) + suffix)
                destination.write_bytes(source.read_bytes())
                os.chmod(destination,0o600)
                copied.append(destination)
        try:
            with closing(connect(copy, 1000, watchdog=5.0)) as connection:
                return {'quick_check': quick_check(connection),
                        'pages': connection.execute('PRAGMA page_count').fetchone()[0],
                        'authoritative': False, 'original_modified': False}
        except (sqlite3.Error, OSError):
            return {'quick_check': None, 'authoritative': False, 'original_modified': False, 'error': 'copy_open_failed'}


def cmd_backup(args):
    path = Path(args.database).absolute()
    database, _header = preflight(path, args.fix_permissions, args.allow_live_user_db)
    backup = online_backup(path, database['identity'], args.backup_dir or default_backup_dir(path), args.busy_timeout_ms)
    return emit(backup)


def cmd_migrate_wal(args):
    path = Path(args.database).absolute()
    database, header = preflight(path, args.fix_permissions, args.allow_live_user_db)
    identity = database['identity']
    result = {'database': str(path), 'before': header['header_mode']}
    if header['header_mode'] == 'wal':
        with closing(open_rw_checked(path, identity, args.busy_timeout_ms)) as connection:
            status = checkpoint_truncate(connection)
        enforce_permissions(path)
        assert_identity(path, identity)
        return emit({'status': 'already_wal', **result, 'checkpoint': status})
    if sidecars(path)['journal']['present']:
        raise MaintError('hot_journal_present')
    backup = online_backup(path, identity, args.backup_dir or default_backup_dir(path), args.busy_timeout_ms)
    result['backup'] = backup
    connection = open_rw_checked(path, identity, args.busy_timeout_ms)
    try:
        quiesce_probe(connection, args.quiesce_retries)
        assert_identity(path, identity)
        set_journal_mode(connection, 'wal')
        checkpoint = checkpoint_truncate(connection)
        check = quick_check(connection)
    except MaintError:
        raise
    except sqlite3.Error as exc:
        raise map_sqlite_error(exc) from None
    finally:
        connection.close()
    enforce_permissions(path)
    assert_identity(path, identity)
    after = read_header(path)
    if after['header_mode'] != 'wal' or check != 'ok':
        raise MaintError('migration_verification_failed', 4)
    return emit({'status': 'migrated', **result, 'after': after['header_mode'],
                 'checkpoint': checkpoint, 'quick_check': check, 'mode': '0o%04o' % FILE_MODE})


def cmd_revert_delete(args):
    path = Path(args.database).absolute()
    database, header = preflight(path, args.fix_permissions, args.allow_live_user_db)
    identity = database['identity']
    result = {'database': str(path), 'before': header['header_mode']}
    if header['header_mode'] == 'delete':
        return emit({'status': 'already_delete', **result})
    backup = online_backup(path, identity, args.backup_dir or default_backup_dir(path), args.busy_timeout_ms)
    result['backup'] = backup
    connection = open_rw_checked(path, identity, args.busy_timeout_ms)
    try:
        checkpoint = checkpoint_truncate(connection)
        assert_identity(path, identity)
        set_journal_mode(connection, 'delete')
        check = quick_check(connection)
    except MaintError:
        raise
    except sqlite3.Error as exc:
        raise map_sqlite_error(exc) from None
    finally:
        connection.close()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        remaining = [s for s, info in sidecars(path).items() if s in ('wal', 'shm') and info['present']]
        if not remaining:
            break
        time.sleep(0.1)
    enforce_permissions(path)
    assert_identity(path, identity)
    # SQLite owns sidecar unlink/checkpoint; never unlink WAL/SHM manually.
    after = read_header(path)
    if after['header_mode'] != 'delete' or check != 'ok':
        raise MaintError('revert_verification_failed', 4)
    leftover = {s: info['present'] for s, info in sidecars(path).items() if s in ('wal', 'shm') and info['present']}
    result_final = {'status': 'reverted', **result, 'after': after['header_mode'],
                    'checkpoint': checkpoint, 'quick_check': check, 'sidecars_remaining': leftover}
    return emit(result_final)


def cmd_checkpoint(args):
    path = Path(args.database).absolute()
    database, header = preflight(path, args.fix_permissions, args.allow_live_user_db)
    identity = database['identity']
    if header['header_mode'] != 'wal':
        return emit({'status': 'not_wal_noop', 'database': str(path)})
    with closing(open_rw_checked(path, identity, args.busy_timeout_ms)) as connection:
        status = checkpoint_truncate(connection)
    enforce_permissions(path)
    assert_identity(path, identity)
    return emit({'status': 'checkpointed', 'database': str(path), 'checkpoint': status})


def build_parser():
    parser = argparse.ArgumentParser(description='Harmonika SQLite journal-mode maintenance (metadata only, never row data).')
    sub = parser.add_subparsers(dest='command', required=True)
    for name, handler in (('inspect', cmd_inspect), ('backup', cmd_backup), ('migrate-wal', cmd_migrate_wal),
                          ('revert-delete', cmd_revert_delete), ('checkpoint', cmd_checkpoint)):
        command = sub.add_parser(name)
        command.add_argument('database')
        command.add_argument('--busy-timeout-ms', type=int, default=2000)
        command.add_argument('--quiesce-retries', type=int, default=3)
        command.add_argument('--backup-dir')
        if handler is not cmd_inspect:
            command.add_argument('--fix-permissions', action='store_true')
            command.add_argument('--allow-live-user-db', action='store_true')
            command.set_defaults(handler=handler)
        else:
            command.add_argument('--copy-diagnostic', action='store_true')
            command.set_defaults(handler=handler, fix_permissions=False, allow_live_user_db=False)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except MaintError as error:
        return fail(error)
    except OSError:
        return fail(MaintError('filesystem_error', 2))


if __name__ == '__main__':
    sys.exit(main())

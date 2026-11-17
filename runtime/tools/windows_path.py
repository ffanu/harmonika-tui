"""Per-user Windows command discovery. No machine PATH, profiles, or processes.

Store injection supports pure fixture tests on any OS. Default registry and
notification APIs are imported only on Windows, at the point of use.
"""
import ctypes
import ntpath
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

MANAGED_HEADER = '@rem HARMONIKA-MANAGED-COMMAND-SHIM v1'
REG_SZ, REG_EXPAND_SZ = 1, 2


class WindowsEnvironmentStore:
    def read_path(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_QUERY_VALUE) as key:
                return winreg.QueryValueEx(key, 'Path')
        except FileNotFoundError:
            return '', winreg.REG_EXPAND_SZ

    def write_path(self, value, kind):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, 'Path', 0, kind, value)


def notify_environment():
    if os.name != 'nt': return False
    try:
        from ctypes import wintypes
        api = ctypes.WinDLL('user32', use_last_error=True).SendMessageTimeoutW
        api.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p,
                        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        api.restype = ctypes.c_ssize_t
        result = ctypes.c_size_t()
        label = ctypes.c_wchar_p('Environment')
        return bool(api(0xffff, 0x1a, 0, ctypes.cast(label, ctypes.c_void_p), 0x2, 2000, ctypes.byref(result)))
    except (OSError, AttributeError): return False


def _normalized(value, env):
    values = {key.upper(): val for key, val in env.items() if isinstance(key, str) and isinstance(val, str)}
    value = value.strip().strip('"').strip()
    for _ in range(8):
        expanded = re.sub(r'%([^%]+)%', lambda match: values.get(match[1].upper(), match[0]), value)
        if expanded == value: break
        value = expanded
    return ntpath.normcase(ntpath.normpath(value)).rstrip('\\/') if value else ''


def _entries(value):
    return value.split(';') if value else []


def merge_user_path(raw, bindir, env):
    """Keep unrelated raw entries and registry expansion syntax exactly as stored."""
    wanted = _normalized(str(bindir), env)
    entries = _entries(raw)
    matches = [item for item in entries if _normalized(item, env) == wanted]
    prefix = matches[0] if matches else str(bindir)
    rest = [item for item in entries if _normalized(item, env) != wanted]
    return ';'.join([prefix, *rest])


def _linklike(path):
    try: info = path.lstat()
    except FileNotFoundError: return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _regular(path):
    try: info = path.lstat()
    except (OSError, ValueError): return False
    return stat.S_ISREG(info.st_mode) and not _linklike(path)


def _canonical_bindir(bindir):
    path = Path(bindir)
    if not path.is_absolute() or '..' in path.parts or any(ch in str(path) for ch in '\x00\r\n;"'):
        raise ValueError('invalid_command_directory')
    if not path.is_dir() or any(_linklike(parent) for parent in (path,*path.parents)) or not _regular(path/'harmonika.cmd'):
        raise ValueError('command_wrapper_missing_or_unsafe')
    # Windows resolves legitimate DOS 8.3 profile/temp names to their long name.
    # Reject links/reparse points before resolution, rather than rejecting that
    # normal spelling expansion. Dispatch only to the returned canonical file.
    canonical=path.resolve(strict=True)
    if not canonical.is_dir() or not _regular(canonical/'harmonika.cmd'):
        raise ValueError('command_wrapper_missing_or_unsafe')
    return canonical


def _shim(wrapper):
    # A .cmd tail dispatch transfers control without CALL's second expansion.
    # Literal percent characters in the installed path must survive cmd parsing.
    target = str(wrapper).replace('%', '%%')
    return (MANAGED_HEADER+'\r\n@echo off\r\n@setlocal DisableDelayedExpansion\r\n'
            '@chcp 65001 >nul\r\n"'+target+'" %*\r\n').encode('utf-8')


def _install_alias(directory, wrapper):
    target = directory/'harmonika.cmd'
    if any(_linklike(parent) for parent in (directory, *directory.parents)):
        return False, False, 'alias_directory_unsafe'
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir(): return False, False, 'alias_directory_unsafe'
    for suffix in ('.com','.exe','.bat','.ps1'):
        other=directory/('harmonika'+suffix)
        if other.exists() or _linklike(other):return False,False,'alias_conflict'
    existing = None
    try:
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or _linklike(target) or info.st_size > 16384 or info.st_nlink != 1:
            return False, False, 'alias_conflict'
        existing = target.read_bytes()
        if not existing.startswith((MANAGED_HEADER+'\r\n').encode('ascii')):
            return False, False, 'alias_conflict'
    except FileNotFoundError: pass
    desired = _shim(wrapper)
    if existing == desired: return True, False, ''
    temporary = directory/('.harmonika-shim-'+uuid4().hex+'.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(desired);stream.flush();os.fsync(stream.fileno())
        if existing is None:
            # Atomic no-overwrite publication, including concurrent unknown files.
            try: os.link(temporary, target)
            except FileExistsError: return False, False, 'alias_conflict'
        else:
            # Recheck immediately before atomic replacement; preserve unknown files.
            if not _regular(target) or target.read_bytes() != existing:
                return False, False, 'alias_conflict'
            os.replace(temporary, target)
        return True, True, ''
    finally:
        temporary.unlink(missing_ok=True)


def configure_windows_command(bindir, *, env=None, store=None, notifier=None, candidates=None):
    env = dict(os.environ if env is None else env)
    result = {'status':'degraded', 'path_persisted':False, 'path_changed':False,
              'environment_notified':False, 'alias_installed':False, 'alias_existing':False,
              'stale_parent_path_ready':False, 'alias':{'status':'unavailable','location':None,'code':''}}
    try: directory = _canonical_bindir(bindir)
    except (OSError, ValueError):
        result['code']='command_wrapper_missing_or_unsafe';return result
    if store is None:
        if os.name != 'nt':result['code']='windows_platform_required';return result
        store = WindowsEnvironmentStore()
    try:
        raw, kind = store.read_path()
        if not isinstance(raw,str) or kind not in (REG_SZ,REG_EXPAND_SZ):
            result['code']='user_path_registry_type_unsupported';return result
        merged = merge_user_path(raw, directory, env)
        if merged != raw:store.write_path(merged, kind);result['path_changed']=True
        result['path_persisted']=True
    except (OSError, ValueError, TypeError):
        result['code']='user_path_persist_failed';return result
    try:result['environment_notified']=bool((notifier or notify_environment)())
    except (OSError, ValueError, TypeError):pass
    original = env.get('HARMONIKA_ORIGINAL_PATH', env.get('PATH',''))
    originals = {_normalized(entry,env) for entry in _entries(original) if entry.strip()}
    if _normalized(str(directory),env) in originals:
        result.update(status='ready',stale_parent_path_ready=True)
        result['alias']['status']='not_needed';return result
    known = []
    if env.get('LOCALAPPDATA'):known.append(('windows_apps',Path(env['LOCALAPPDATA'])/'Microsoft/WindowsApps'))
    if env.get('APPDATA'):known.append(('npm',Path(env['APPDATA'])/'npm'))
    all_known=list(known)
    original_order=[_normalized(entry,env) for entry in _entries(original) if entry.strip()]
    if candidates is not None:
        requested = {_normalized(str(candidate),env) for candidate in candidates}
        known = [(label,path) for label,path in known if _normalized(str(path),env) in requested]
    for label,path in known:
        if not path.is_absolute() or _normalized(str(path),env) not in originals:continue
        try: ready, changed, code = _install_alias(path,directory/'harmonika.cmd')
        except (OSError, ValueError): ready,changed,code=False,False,'alias_write_failed'
        if not ready:result['alias']['code']=code;continue
        index=original_order.index(_normalized(str(path),env))
        shadowed=any(_normalized(str(other),env) in original_order[:index] and
                     any((other/('harmonika'+suffix)).exists() or _linklike(other/('harmonika'+suffix))
                         for suffix in ('.com','.exe','.bat','.cmd','.ps1'))
                     for _,other in all_known)
        result.update(status='shell_refresh_required' if shadowed else 'ready',
                      stale_parent_path_ready=not shadowed,alias_installed=True,alias_existing=not changed)
        result['alias']={'status':'shadowed' if shadowed else 'ready','location':label,'created':changed,
                         'code':'earlier_user_command_preserved' if shadowed else ''}
        return result
    result['status']='shell_refresh_required'
    if not result['alias']['code']:result['alias']['code']='no_existing_user_path_directory'
    return result

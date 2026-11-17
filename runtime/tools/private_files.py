"""Read-only private-file validation for POSIX permissions and Windows ACLs.

Never changes key/config ownership or ACLs. All Windows security queries are
standard-library ctypes calls; no password, key contents or ACLs are logged.
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path, PureWindowsPath
import re
import stat

TRUSTED_SYSTEM_SIDS = frozenset(('S-1-5-18', 'S-1-5-32-544'))
MUTATING_ACCESS = 0x01000000 | 0x02000000 | 0x0002 | 0x0004 | 0x0010 | 0x0040 | 0x0100 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000


class UnsafeFile(ValueError): pass


def is_linklike(path):
    try:
        value = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, 'st_file_attributes', 0) & 0x400)


def acl_is_private(snapshot, private=True):
    """Conservative DACL policy; unknown/object/callback ACEs fail closed."""
    if not isinstance(snapshot, dict): return False
    user = snapshot.get('current_user')
    if not isinstance(user, str) or not re.fullmatch(r'S-1-(?:\d+-)+\d+', user): return False
    trusted = TRUSTED_SYSTEM_SIDS | {user}
    if snapshot.get('owner') not in trusted or snapshot.get('dacl_present') is not True:
        return False
    entries = snapshot.get('entries')
    if not isinstance(entries, list) or not entries: return False
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('type') not in ('allow', 'deny'):
            return False
        if type(entry.get('flags')) is not int or type(entry.get('mask')) is not int:
            return False
        if not 0 <= entry['flags'] <= 255 or not 0 <= entry['mask'] <= 0xFFFFFFFF:
            return False
        if entry['flags'] & 0x08 or entry['type'] == 'deny':  # INHERIT_ONLY / denied rights do not grant file access.
            continue
        if entry.get('sid') not in trusted and entry['mask']:
            if private or entry['mask'] & MUTATING_ACCESS:
                return False
    return True


def windows_security(path):
    if os.name != 'nt': raise UnsafeFile('windows_security_unavailable')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    advapi = C.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE];kernel.CloseHandle.restype = W.BOOL
    kernel.LocalFree.argtypes = [C.c_void_p];kernel.LocalFree.restype = C.c_void_p
    advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    advapi.OpenProcessToken.restype = W.BOOL
    advapi.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    advapi.GetTokenInformation.restype = W.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = W.BOOL
    advapi.GetNamedSecurityInfoW.argtypes = [W.LPCWSTR, C.c_int, W.DWORD] + [C.POINTER(C.c_void_p)] * 5
    advapi.GetNamedSecurityInfoW.restype = W.DWORD
    advapi.IsValidAcl.argtypes = [C.c_void_p];advapi.IsValidAcl.restype = W.BOOL
    advapi.GetAce.argtypes = [C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)];advapi.GetAce.restype = W.BOOL
    advapi.IsValidSid.argtypes = [C.c_void_p];advapi.IsValidSid.restype = W.BOOL
    advapi.GetLengthSid.argtypes = [C.c_void_p];advapi.GetLengthSid.restype = W.DWORD
    token = W.HANDLE();descriptor = C.c_void_p()
    def sid_text(sid):
        text = W.LPWSTR()
        if not sid or not advapi.IsValidSid(sid) or not advapi.ConvertSidToStringSidW(sid, C.byref(text)):
            raise UnsafeFile('windows_security_unavailable')
        try:return text.value
        finally:kernel.LocalFree(C.cast(text, C.c_void_p))
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, C.byref(token)):
        raise UnsafeFile('windows_security_unavailable')
    try:
        length = W.DWORD();advapi.GetTokenInformation(token, 1, None, 0, C.byref(length))
        if not 1 <= length.value <= 65536:raise UnsafeFile('windows_security_unavailable')
        buffer = C.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, buffer, len(buffer), C.byref(length)):
            raise UnsafeFile('windows_security_unavailable')
        user = sid_text(C.cast(buffer, C.POINTER(C.c_void_p))[0])
        owner, dacl = C.c_void_p(), C.c_void_p()
        status = advapi.GetNamedSecurityInfoW(str(path), 1, 0x5, C.byref(owner), None, C.byref(dacl), None, C.byref(descriptor))
        if status:raise UnsafeFile('windows_security_unavailable')
        result = {'current_user': user, 'owner': sid_text(owner), 'dacl_present': bool(dacl), 'entries': []}
        if not dacl:return result
        if not advapi.IsValidAcl(dacl):raise UnsafeFile('windows_security_unavailable')
        class ACL(C.Structure):
            _fields_ = [('revision', W.BYTE), ('reserved', W.BYTE), ('size', W.WORD), ('count', W.WORD), ('reserved2', W.WORD)]
        acl = C.cast(dacl, C.POINTER(ACL)).contents
        if acl.count > 4096:raise UnsafeFile('windows_security_unavailable')
        for index in range(acl.count):
            ace = C.c_void_p()
            if not advapi.GetAce(dacl, index, C.byref(ace)):
                raise UnsafeFile('windows_security_unavailable')
            kind = W.BYTE.from_address(ace.value).value
            flags = W.BYTE.from_address(ace.value + 1).value
            size = W.WORD.from_address(ace.value + 2).value
            if kind not in (0, 1) or size < 20:
                raise UnsafeFile('windows_security_unavailable')
            sid = C.c_void_p(ace.value + 8)
            if not advapi.IsValidSid(sid) or advapi.GetLengthSid(sid) > size - 8:
                raise UnsafeFile('windows_security_unavailable')
            result['entries'].append({'type': 'allow' if kind == 0 else 'deny', 'flags': flags,
                                      'mask': W.DWORD.from_address(ace.value + 4).value, 'sid': sid_text(sid)})
        return result
    finally:
        kernel.CloseHandle(token)
        if descriptor:kernel.LocalFree(descriptor)


def private_file(path, private=True, security_reader=None):
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts or any(is_linklike(x) for x in (p, *p.parents)):
        raise UnsafeFile('unsafe_config_file')
    info = p.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024*1024:
        raise UnsafeFile('unsafe_config_file')
    if os.name == 'nt':
        lexical = PureWindowsPath(str(p))
        if not re.fullmatch(r'[A-Za-z]:', lexical.drive) or not lexical.root or os.path.normcase(str(p.resolve())) != os.path.normcase(str(p)):
            raise UnsafeFile('unsafe_config_file')
        for part in lexical.parts[1:]:
            if ':' in part or part.endswith((' ', '.')):
                raise UnsafeFile('unsafe_config_file')
        if not acl_is_private((security_reader or windows_security)(p), private):
            raise UnsafeFile('unsafe_config_file')
    elif os.name == 'posix':
        if info.st_uid != os.getuid() or info.st_mode & (0o077 if private else 0o022):
            raise UnsafeFile('unsafe_config_file')
    else:raise UnsafeFile('ssh_platform_not_supported')
    return p

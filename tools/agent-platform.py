"""Standard-library Windows helpers for private state and process identity.

No credential, checkpoint contents, task text or command output is read here.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import stat


def _dlls():
    if os.name != 'nt':
        raise RuntimeError('windows_helper_only')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    advapi = C.WinDLL('advapi32', use_last_error=True)
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.CloseHandle.restype = W.BOOL
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p
    return kernel, advapi


def process_birth(pid):
    kernel, _ = _dlls()
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only.
    if not handle:
        return '' if C.get_last_error() == 87 else None
    try:
        created, exited, system, user = (W.FILETIME() for _ in range(4))
        kernel.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
        kernel.GetProcessTimes.restype = W.BOOL
        if not kernel.GetProcessTimes(handle, C.byref(created), C.byref(exited), C.byref(system), C.byref(user)):
            return None
        if exited.dwLowDateTime or exited.dwHighDateTime:
            return ''
        return 'windows:' + str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def private_state(directory):
    kernel, advapi = _dlls()
    root = Path(directory)
    root_stat = root.lstat()
    key = root / '.key'
    if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_file_attributes & 0x400:
        raise RuntimeError('state_symlink_denied')
    if key.exists() or key.is_symlink():
        info = key.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_file_attributes & 0x400:
            raise RuntimeError('state_symlink_denied')
    kernel.GetCurrentProcess.restype = W.HANDLE
    advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    advapi.OpenProcessToken.restype = W.BOOL
    advapi.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    advapi.GetTokenInformation.restype = W.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = W.BOOL
    token = W.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, C.byref(token)):
        raise RuntimeError('state_privacy_unavailable')
    sid_text = W.LPWSTR()
    descriptor = C.c_void_p()
    try:
        required = W.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, C.byref(required))
        buffer = C.create_string_buffer(required.value)
        if not advapi.GetTokenInformation(token, 1, buffer, len(buffer), C.byref(required)):
            raise RuntimeError('state_privacy_unavailable')
        sid = C.cast(buffer, C.POINTER(C.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, C.byref(sid_text)):
            raise RuntimeError('state_privacy_unavailable')
        # Inherit only to this user's children and SYSTEM. Do not change a user
        # profile or any ancestor directory; this applies solely to agent state.
        sddl = 'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;' + sid_text.value + ')'
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = W.BOOL
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None):
            raise RuntimeError('state_privacy_unavailable')
        present, defaulted = W.BOOL(), W.BOOL()
        dacl = C.c_void_p()
        advapi.GetSecurityDescriptorDacl.argtypes = [C.c_void_p, C.POINTER(W.BOOL), C.POINTER(C.c_void_p), C.POINTER(W.BOOL)]
        advapi.GetSecurityDescriptorDacl.restype = W.BOOL
        if not advapi.GetSecurityDescriptorDacl(descriptor, C.byref(present), C.byref(dacl), C.byref(defaulted)) or not present.value:
            raise RuntimeError('state_privacy_unavailable')
        advapi.SetNamedSecurityInfoW.argtypes = [W.LPWSTR, C.c_int, W.DWORD, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
        advapi.SetNamedSecurityInfoW.restype = W.DWORD
        for target in [root] + ([key] if key.exists() else []):
            if advapi.SetNamedSecurityInfoW(str(target), 1, 0x80000004, None, None, dacl, None):
                raise RuntimeError('state_privacy_unavailable')
    finally:
        kernel.CloseHandle(token)
        if sid_text:
            kernel.LocalFree(C.cast(sid_text, C.c_void_p))
        if descriptor:
            kernel.LocalFree(descriptor)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--birth', type=int)
    group.add_argument('--private-state')
    args = parser.parse_args()
    if args.birth is not None:
        if args.birth <= 0 or args.birth > 0xFFFFFFFF:
            raise RuntimeError('invalid_process_id')
        print(json.dumps({'birth': process_birth(args.birth)}))
    else:
        private_state(args.private_state)
        print('{"ok":true}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('state_platform_helper_failed')

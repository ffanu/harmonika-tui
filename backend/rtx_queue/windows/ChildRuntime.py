"""Run one configured app with durable null stdio, independent of parent pipes."""
import os
from pathlib import Path
import runpy
import sys


def main():
    null = os.open(os.devnull, os.O_RDWR)
    try:
        for descriptor in (0, 1, 2):
            os.dup2(null, descriptor)
    finally:
        if null > 2:
            os.close(null)
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        import msvcrt
        set_handle = ctypes.WinDLL('kernel32', use_last_error=True).SetStdHandle
        set_handle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        set_handle.restype = wintypes.BOOL
        for constant, descriptor in ((-10, 0), (-11, 1), (-12, 2)):
            if not set_handle(wintypes.DWORD(constant), wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))):
                raise SystemExit(2)
    arguments = sys.argv[1:]
    if arguments and arguments[0] == '-u':
        arguments = arguments[1:]
    if not arguments:
        raise SystemExit(2)
    script = Path(arguments[0]).resolve(strict=True)
    sys.argv = [str(script), *arguments[1:]]
    sys.path[0] = str(script.parent)
    runpy.run_path(str(script), run_name='__main__')


if __name__ == '__main__':
    main()

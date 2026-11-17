"""Native Windows console regressions; every credential below is synthetic.

ConPTY deliberately gives the real Python child a console (isatty=True), unlike
subprocess pipes or mocked input. No account, provider, network, or user config
is used. Based on Microsoft's Creating a Pseudoconsole Session API example.
"""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1]
KEY = 'hai_live_' + 'synthetic_console_fixture_1234567890'


class COORD(ctypes.Structure):
    _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR),
                ('lpTitle', w.LPWSTR), ('dwX', w.DWORD), ('dwY', w.DWORD),
                ('dwXSize', w.DWORD), ('dwYSize', w.DWORD),
                ('dwXCountChars', w.DWORD), ('dwYCountChars', w.DWORD),
                ('dwFillAttribute', w.DWORD), ('dwFlags', w.DWORD),
                ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                ('lpReserved2', ctypes.POINTER(w.BYTE)), ('hStdInput', w.HANDLE),
                ('hStdOutput', w.HANDLE), ('hStdError', w.HANDLE)]


class STARTUPINFOEX(ctypes.Structure):
    _fields_ = [('StartupInfo', STARTUPINFO), ('lpAttributeList', w.LPVOID)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', w.HANDLE), ('hThread', w.HANDLE),
                ('dwProcessId', w.DWORD), ('dwThreadId', w.DWORD)]


class NativeConsole:
    """Own exactly one ConPTY and child, and continuously drain output."""
    def __init__(self, command, env, cwd):
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreatePipe': ([ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), w.LPVOID, w.DWORD], w.BOOL),
            'CreatePseudoConsole': ([COORD, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], ctypes.c_long),
            'ClosePseudoConsole': ([w.HANDLE], None),
            'InitializeProcThreadAttributeList': ([w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
            'UpdateProcThreadAttribute': ([w.LPVOID, w.DWORD, ctypes.c_size_t, w.LPVOID, ctypes.c_size_t, w.LPVOID, w.LPVOID], w.BOOL),
            'DeleteProcThreadAttributeList': ([w.LPVOID], None),
            'CreateProcessW': ([w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION)], w.BOOL),
            'ReadFile': ([w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL),
            'WriteFile': ([w.HANDLE, w.LPCVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
            'GetExitCodeProcess': ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            'TerminateProcess': ([w.HANDLE, w.UINT], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            api = getattr(self.k, name)
            api.argtypes, api.restype = args, result
        self.handles = []
        self.hpc = w.HANDLE()
        self.process = PROCESS_INFORMATION()
        self.reader = None
        self.output = bytearray()
        self.lock = threading.Lock()
        self.attr = None
        self.attr_initialized = False
        try:
            input_read, self.input_write = self.pipe()
            self.output_read, output_write = self.pipe()
            hr = self.k.CreatePseudoConsole(COORD(180, 50), input_read, output_write, 0, ctypes.byref(self.hpc))
            if hr < 0:
                raise OSError('CreatePseudoConsole HRESULT ' + hex(hr & 0xffffffff))
            # Dedicated draining prevents ClosePseudoConsole/output deadlocks.
            self.reader = threading.Thread(target=self.drain, daemon=True)
            self.reader.start()
            required = ctypes.c_size_t()
            self.k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(required))
            self.attr = ctypes.create_string_buffer(required.value)
            self.checked(self.k.InitializeProcThreadAttributeList(self.attr, 1, 0, ctypes.byref(required)))
            self.attr_initialized = True
            self.checked(self.k.UpdateProcThreadAttribute(self.attr, 0, 0x00020016, self.hpc,
                                                         ctypes.sizeof(w.HANDLE), None, None))
            startup = STARTUPINFOEX()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            # Match Windows Terminal's ConptyConnection::_LaunchAttachedClient:
            # without this flag, CreateProcess duplicates redirected runner
            # stdin/stdout even with bInheritHandles=False, bypassing ConPTY.
            # Explicit NULL std handles are replaced by real console handles.
            # https://github.com/microsoft/terminal/discussions/15814
            startup.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
            startup.lpAttributeList = ctypes.cast(self.attr, w.LPVOID)
            environment = ctypes.create_unicode_buffer('\0'.join(k + '=' + v for k, v in sorted(env.items(), key=lambda x: x[0].upper())) + '\0\0')
            line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            self.checked(self.k.CreateProcessW(None, line, None, None, False, 0x00080000 | 0x00000400,
                                              environment, str(cwd), ctypes.byref(startup.StartupInfo), ctypes.byref(self.process)))
            for handle in (input_read, output_write):
                self.close_handle(handle)
        except Exception:
            self.close()
            raise

    def checked(self, ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def pipe(self):
        read, write = w.HANDLE(), w.HANDLE()
        self.checked(self.k.CreatePipe(ctypes.byref(read), ctypes.byref(write), None, 65536))
        self.handles.extend((read, write))
        return read, write

    def close_handle(self, handle):
        if handle in self.handles:
            self.k.CloseHandle(handle)
            self.handles.remove(handle)

    def drain(self):
        buffer, size = ctypes.create_string_buffer(8192), w.DWORD()
        while self.k.ReadFile(self.output_read, buffer, len(buffer), ctypes.byref(size), None) and size.value:
            with self.lock:
                self.output.extend(buffer.raw[:size.value])

    def text(self):
        with self.lock:
            raw = bytes(self.output)
        return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw.decode('utf-8', errors='replace'))

    def wait_for(self, marker, seconds=15):
        deadline = time.monotonic() + seconds
        exited_at = None
        while time.monotonic() < deadline:
            if marker in self.text():
                return
            if self.k.WaitForSingleObject(self.process.hProcess, 0) == 0:
                # Output can trail process exit while the draining thread runs.
                exited_at = exited_at or time.monotonic()
                if time.monotonic() - exited_at > 0.5:
                    raise AssertionError('console child exited before expected prompt')
            time.sleep(0.025)
        raise AssertionError('console prompt timeout')

    def send(self, data):
        count = w.DWORD()
        self.checked(self.k.WriteFile(self.input_write, data, len(data), ctypes.byref(count), None))
        if count.value != len(data):
            raise AssertionError('incomplete synthetic console input')

    def wait(self, seconds=15):
        if self.k.WaitForSingleObject(self.process.hProcess, int(seconds * 1000)) != 0:
            raise AssertionError('console child exit timeout')
        result = w.DWORD()
        self.checked(self.k.GetExitCodeProcess(self.process.hProcess, ctypes.byref(result)))
        return result.value

    def close(self):
        if self.process.hProcess:
            if self.k.WaitForSingleObject(self.process.hProcess, 0) != 0:
                # Only the child just created by this fixture; never a user PID.
                self.k.TerminateProcess(self.process.hProcess, 99)
                self.k.WaitForSingleObject(self.process.hProcess, 5000)
            self.k.CloseHandle(self.process.hThread)
            self.k.CloseHandle(self.process.hProcess)
            self.process.hProcess = None
        if self.hpc:
            closed = threading.Event()
            hpc = self.hpc
            def close_console():
                self.k.ClosePseudoConsole(hpc)
                closed.set()
            closer = threading.Thread(target=close_console, daemon=True)
            closer.start()
            if not closed.wait(5):
                raise AssertionError('ConPTY cleanup timeout')
            self.hpc = w.HANDLE()
        if self.reader:
            self.reader.join(3)
        if self.attr_initialized:
            self.k.DeleteProcThreadAttributeList(self.attr)
            self.attr_initialized = False
            self.attr = None
        for handle in self.handles[:]:
            self.close_handle(handle)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def isolated_env(root):
    env = {k: v for k, v in os.environ.items() if not k.startswith('HARMONIKA_')}
    for name in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ZAI_API_KEY'):
        env.pop(name, None)
    env.update(HARMONIKA_GLOBAL_CONFIG=str(root/'config'), HARMONIKA_CONFIG_DIR=str(root/'config'),
               HARMONIKA_GLOBAL_DATA=str(root/'data'), HARMONIKA_TERMINAL_HOME=str(root/'terminal'),
               HARMONIKA_BIN_DIR=str(root/'bin'), HARMONIKA_DOWNLOADS_DIR=str(root/'downloads'),
               HARMONIKA_TOOLS_HOME=str(root/'tools'), HARMONIKA_LAUNCH_AFTER_INSTALL='0',
               HARMONIKA_NONINTERACTIVE='0', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    return env


@unittest.skipUnless(sys.platform == 'win32', 'native Windows ConPTY required')
class WindowsLicenseConsoleTests(unittest.TestCase):
    def test_powershell5_python_probe_continues_after_native_stderr(self):
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        with tempfile.TemporaryDirectory(prefix='harmonika probe ') as temp:
            root=Path(temp)
            broken=root/'broken-python.cmd'
            broken.write_text('@echo off\r\necho SYNTHETIC_PYTHON_PROBE_ERROR 1>&2\r\nexit /b 37\r\n',encoding='ascii')
            quote=lambda value:"'"+str(value).replace("'","''")+"'"
            expression="""$ErrorActionPreference='Stop';$tokens=$null;$errors=$null;
$ast=[Management.Automation.Language.Parser]::ParseFile(SCRIPT,[ref]$tokens,[ref]$errors);
if($errors.Count){throw 'fixture_parse_failed'}
$definition=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-HarmonikaPython'},$true);
if(-not $definition){throw 'python_probe_missing'}
Invoke-Expression $definition.Extent.Text;
if(Test-HarmonikaPython -Executable BROKEN -Prefix @()){throw 'broken_probe_accepted'}
if(-not (Test-HarmonikaPython -Executable PYTHON -Prefix @())){throw 'healthy_fallback_rejected'}
if($ErrorActionPreference -ne 'Stop'){throw 'caller_error_policy_changed'}
Write-Output 'PROBE_OK';
""".replace('SCRIPT',quote(SOURCE/'scripts/install.ps1')).replace('BROKEN',quote(broken)).replace('PYTHON',quote(sys.executable))
            run=subprocess.run([str(powershell),'-NoLogo','-NoProfile','-Command',expression],cwd=root,env=isolated_env(root),stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,'PowerShell Python fallback probe failed')
            self.assertIn('PROBE_OK',run.stdout)

    def fixture(self, root):
        child = root/'console child.py'
        child.write_text('''import importlib.util,json,pathlib,sys
assert sys.stdin.isatty(), 'ConPTY stdin must be an actual console'
spec=importlib.util.spec_from_file_location('fixture_license',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
result=pathlib.Path(sys.argv[2])
def activate(key,config=None):
 result.write_text(json.dumps({'key':key,'isatty':sys.stdin.isatty()}),encoding='utf-8')
 return {'coding':True,'saved':True}
m.activate=activate
m.device_name=lambda:'Synthetic Windows Console'
m.interactive_tui_box(pathlib.Path(sys.argv[3]))
print('FIXTURE_COMPLETED',flush=True)
''', encoding='utf-8')
        result = root/'result.json'
        command = [sys.executable, '-u', str(child), str(SOURCE/'runtime/license.py'), str(result), str(root/'config')]
        return command, result

    def test_visible_paste_is_intact_and_brand_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix='harmonika console Ω ') as temp:
            root = Path(temp)
            command, result = self.fixture(root)
            with NativeConsole(command, isolated_env(root), root) as console:
                console.wait_for('Masukkan API Key Lisensi Harmonika')
                before = console.text()
                self.assertIn('Selamat datang di Harmonika Ai Terminal (TUI)!', before)
                self.assertIn('1 API Key = 1 Perangkat (Single Device)', before)
                self.assertIn('█   █', before)
                self.assertIn('╭────────────────', before)
                console.send(KEY.encode('ascii') + b'\r')
                self.assertEqual(console.wait(), 0)
                console.wait_for(KEY)
                self.assertTrue(result.is_file(), 'synthetic activation receipt missing')
                receipt = json.loads(result.read_text(encoding='utf-8'))
                self.assertTrue(receipt['isatty'])
                self.assertEqual(receipt['key'], KEY)

    def test_ctrl_c_cancels_without_activation(self):
        self.cancel_case(b'\x03')

    def test_windows_console_eof_cancels_without_activation(self):
        self.cancel_case(b'\x1a\r')

    def cancel_case(self, gesture):
        with tempfile.TemporaryDirectory(prefix='harmonika cancel Ω ') as temp:
            root = Path(temp)
            command, result = self.fixture(root)
            with NativeConsole(command, isolated_env(root), root) as console:
                console.wait_for('Masukkan API Key Lisensi Harmonika')
                console.send(gesture)
                self.assertEqual(console.wait(), 1)
                self.assertFalse(result.exists(), 'cancellation unexpectedly activated a key')

    @unittest.skipUnless(shutil.which('git') and shutil.which('node'), 'installer prerequisites needed')
    def test_powershell5_pipeline_invokes_native_console_onboarding(self):
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        self.assertTrue(powershell.is_file(), 'Windows PowerShell 5.1 is required by this test')
        with tempfile.TemporaryDirectory(prefix='harmonika PS5 Ω ') as temp:
            root = Path(temp)
            upstream, source = root/'upstream', root/'source'
            (upstream/'runtime').mkdir(parents=True)
            receipt = root/'ps5-receipt.json'
            (upstream/'runtime/license.py').write_text('''import json,os,pathlib,sys
assert sys.argv[1:]==['ensure']
assert sys.stdin.isatty(), 'PowerShell lost native console stdin'
key=input('SYNTHETIC_PS5_KEY: ')
pathlib.Path(os.environ['FIXTURE_RECEIPT']).write_text(json.dumps({'key':key,'isatty':True}),encoding='utf-8')
''', encoding='utf-8')
            (upstream/'runtime/install.py').write_text("import os,pathlib\npathlib.Path(os.environ['FIXTURE_INSTALLED']).write_text('installed',encoding='utf-8')\n", encoding='utf-8')
            env = isolated_env(root)
            env.update(HARMONIKA_SOURCE_DIR=str(source), FIXTURE_RECEIPT=str(receipt), FIXTURE_INSTALLED=str(root/'installed'))
            def git(*args):
                subprocess.run(['git', *args], env=env, cwd=root, check=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            git('init', str(upstream))
            git('-C', str(upstream), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'add', '.')
            git('-C', str(upstream), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Synthetic offline fixture')
            git('clone', str(upstream), str(source))
            script_path = str(SOURCE/'scripts/install.ps1').replace("'", "''")
            expression = "[IO.File]::ReadAllText('" + script_path + "',[Text.Encoding]::UTF8) | Invoke-Expression"
            command = [str(powershell), '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', expression]
            with NativeConsole(command, env, root) as console:
                console.wait_for('SYNTHETIC_PS5_KEY:', seconds=30)
                console.send(KEY.encode('ascii') + b'\r')
                self.assertEqual(console.wait(seconds=30), 0)
                self.assertTrue((root/'installed').is_file(), 'PowerShell did not reach fixture installation')
                self.assertEqual(json.loads(receipt.read_text(encoding='utf-8')), {'key':KEY,'isatty':True})


if __name__ == '__main__':
    unittest.main()

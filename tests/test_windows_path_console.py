"""Actual new Windows shell lookup with a stale inherited PATH.

Only isolated fixture directories and an in-memory registry store are changed.
No actual HKCU Environment value, System32 file, license, or provider is used.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
MARKER = 'HARMONIKA_PATH_FIXTURE_V1'


class MemoryPathStore:
    def __init__(self, value):
        self.value, self.kind = value, 2  # REG_EXPAND_SZ; never touch winreg.
        self.writes = []

    def read_path(self):
        return self.value, self.kind

    def write_path(self, value, kind):
        self.value, self.kind = value, kind
        self.writes.append((value, kind))


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(sys.platform == 'win32', 'actual Windows command discovery required')
class WindowsPathConsoleTests(unittest.TestCase):
    def fixture(self, root):
        system = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32'
        alias = root/'local app Ω'/'Microsoft/WindowsApps'
        alias.mkdir(parents=True)
        # Real percent variable syntax would expose recursive CALL expansion.
        terminal = root/'profil Ω %HARMONIKA_PATH_SENTINEL% & !literal!'/'terminal'
        terminal.mkdir(parents=True)
        bindir = terminal.parent/'canonical bin'
        bindir.mkdir()
        launcher = terminal/'launcher.py'
        launcher.write_text("import json,sys\nif sys.argv[1:]==['--version']: print('" + MARKER + "')\nelse: print(json.dumps(sys.argv[1:],ensure_ascii=True))\n", encoding='utf-8')
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('HARMONIKA_')}
        env.update(HARMONIKA_TERMINAL_HOME=str(terminal), HARMONIKA_GLOBAL_CONFIG=str(root/'config'),
                   HARMONIKA_CONFIG_DIR=str(root/'config'), HARMONIKA_GLOBAL_DATA=str(root/'data'),
                   HARMONIKA_BIN_DIR=str(bindir), HARMONIKA_DOWNLOADS_DIR=str(root/'downloads'),
                   HARMONIKA_TOOLS_HOME=str(root/'tools'), LOCALAPPDATA=str(root/'local app Ω'),
                   APPDATA=str(root/'roaming Ω'), HARMONIKA_PATH_SENTINEL='INCORRECT_PATH_EXPANSION',
                   HARMONIKA_NESTED_EXPANSION='%HARMONIKA_SECOND_EXPANSION%',
                   HARMONIKA_SECOND_EXPANSION='INCORRECT_SECOND_EXPANSION',
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        # Avoid ambiguous PATH/Path duplicates in a Unicode environment block.
        for name in list(env):
            if name.upper() == 'PATH':
                env.pop(name)
        env['PATH'] = str(alias) + os.pathsep + str(system)
        env['HARMONIKA_ORIGINAL_PATH'] = env['PATH']
        node = next((value for key, value in os.environ.items() if key == 'HARMONIKA_NODE'), None)
        if not node:
            import shutil
            node = shutil.which('node')
        self.assertTrue(node, 'real Node prerequisite missing')
        env['HARMONIKA_NODE'] = node
        with patch.dict(os.environ, env, clear=True):
            installer = load_module('fixture_windows_installer', SOURCE/'runtime/install.py')
            installer.write_windows_wrappers(root=terminal)
        helper = load_module('fixture_windows_path', SOURCE/'runtime/windows_path.py')
        return env, bindir, alias, helper, system

    def run_shell(self, command, env):
        result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, encoding='utf-8', errors='replace')
        self.assertEqual(result.returncode, 0, 'fixture Windows shell failed; exit=' + str(result.returncode))
        return result.stdout.strip()

    def powershell(self, system, expression, env):
        return self.run_shell([str(system/'WindowsPowerShell/v1.0/powershell.exe'), '-NoLogo',
                               '-NoProfile', '-NonInteractive', '-Command', expression], env)

    def cmd(self, system, payload, env, delayed=False):
        # CMD parses this itself; passing a list would add MSVCRT backslashes.
        command = subprocess.list2cmdline([str(system/'cmd.exe')])
        command += ' /d /v:' + ('on' if delayed else 'off') + ' /s /c "' + payload + '"'
        return self.run_shell(command, env)

    def configure(self, helper, bindir, alias, env):
        store = MemoryPathStore('%USERPROFILE%\\existing user tools')
        notifications = []
        result = helper.configure_windows_command(bindir, env=env, store=store,
                                                   notifier=lambda: notifications.append(True) or True,
                                                   candidates=[alias])
        self.assertTrue(result['path_persisted'])
        self.assertTrue(result['stale_parent_path_ready'])
        self.assertTrue(notifications, 'environment notification was not requested')
        self.assertEqual(store.kind, 2, 'registry value type changed')
        self.assertIn('%USERPROFILE%\\existing user tools', store.value)
        return result

    def test_new_powershell_and_cmd_resolve_with_original_stale_path(self):
        with tempfile.TemporaryDirectory(prefix='harmonika lookup Ω ') as temp:
            env, bindir, alias, helper, system = self.fixture(Path(temp))
            stale = dict(env)
            self.configure(helper, bindir, alias, env)
            self.assertNotIn(str(bindir), stale['PATH'])
            expression = "$ErrorActionPreference='Stop'; harmonika --version; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}"
            self.assertEqual(self.powershell(system, expression, stale), MARKER)
            self.assertEqual(self.cmd(system, 'harmonika --version', stale), MARKER)
            self.assertEqual(self.cmd(system, 'harmonika --version', stale, delayed=True), MARKER)
            current = dict(stale, PATH=str(bindir) + os.pathsep + stale['PATH'])
            self.assertEqual(self.powershell(system, expression, current), MARKER)

    def test_managed_shim_does_not_reparse_arguments_with_call(self):
        with tempfile.TemporaryDirectory(prefix='harmonika arguments Ω ') as temp:
            env, bindir, alias, helper, system = self.fixture(Path(temp))
            stale = dict(env)
            self.configure(helper, bindir, alias, env)
            args = '--fixture-args "literal spaces" "amp & bang!" "%HARMONIKA_NESTED_EXPANSION%"'
            direct_env = dict(stale, HARMONIKA_FIXTURE_COMMAND=str(bindir/'harmonika.cmd'))
            # Insert the path through one environment substitution so literal
            # percent signs are data, rather than syntax typed into CMD.
            direct = self.cmd(system, '"%HARMONIKA_FIXTURE_COMMAND%" ' + args, direct_env)
            via_alias = self.cmd(system, 'harmonika ' + args, stale)
            expected = ['--fixture-args', 'literal spaces', 'amp & bang!', '%HARMONIKA_SECOND_EXPANSION%']
            self.assertEqual(json.loads(direct), expected)
            self.assertEqual(json.loads(via_alias), expected)

    def test_existing_unmanaged_alias_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='harmonika alias preserve Ω ') as temp:
            env, bindir, alias, helper, system = self.fixture(Path(temp))
            existing = alias/'harmonika.cmd'
            sentinel = b'@echo off\r\n@rem OTHER-APPLICATION-OWNED\r\n'
            existing.write_bytes(sentinel)
            helper.configure_windows_command(bindir, env=env, store=MemoryPathStore(''),
                                             notifier=lambda: True, candidates=[alias])
            self.assertEqual(existing.read_bytes(), sentinel)

    def test_system_directory_candidate_is_refused(self):
        with tempfile.TemporaryDirectory(prefix='harmonika System32 guard Ω ') as temp:
            root = Path(temp)
            env, bindir, alias, helper, system = self.fixture(root)
            fake_system = root/'Windows/System32'
            fake_system.mkdir(parents=True)
            existing = fake_system/'harmonika.cmd'
            sentinel = b'@rem SYSTEM-DIRECTORY-FIXTURE\r\n'
            existing.write_bytes(sentinel)
            env.update(SystemRoot=str(root/'Windows'), PATH=str(fake_system),
                       HARMONIKA_ORIGINAL_PATH=str(fake_system))
            helper.configure_windows_command(bindir, env=env, store=MemoryPathStore(''),
                                             notifier=lambda: True, candidates=[fake_system])
            self.assertEqual(existing.read_bytes(), sentinel)


if __name__ == '__main__':
    unittest.main()

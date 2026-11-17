"""Exercise piped shell onboarding through a real controlling terminal."""
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

SOURCE=Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.name=='posix' and shutil.which('node'),'POSIX controlling terminal')
class PipedInstallerOnboarding(unittest.TestCase):
    def fixture(self,root):
        source=root/'source';(source/'.git').mkdir(parents=True);(source/'runtime').mkdir()
        bindir=root/'bin';bindir.mkdir()
        git=bindir/'git';git.write_text('#!/bin/sh\nexit 0\n');git.chmod(0o755)
        # Synthetic verifier: prompt is read from the controlling TTY, never
        # from the downloaded shell program. No real credential or API call.
        (source/'runtime/license.py').write_text("import getpass,sys\nassert sys.argv[1:] == ['ensure']\nassert sys.stdin.isatty()\nprint('LICENSE_FIRST',flush=True)\nassert getpass.getpass('PRIVATE_KEY: ') == 'synthetic-key'\nprint('LICENSE_VALID',flush=True)\n")
        (source/'runtime/install.py').write_text("print('RUNTIME_INSTALLED',flush=True)\n")
        tui=bindir/'harmonika';tui.write_text('#!/bin/sh\necho HARMONIKA_TUI_STARTED\n');tui.chmod(0o755)
        return dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'],HARMONIKA_SOURCE_DIR=str(source),HARMONIKA_BIN_DIR=str(bindir),HARMONIKA_NONINTERACTIVE='0',HARMONIKA_LAUNCH_AFTER_INSTALL='1')

    def test_curl_style_pipe_prompts_before_install_and_opens_tui(self):
        import pty
        with tempfile.TemporaryDirectory() as temp:
            env=self.fixture(Path(temp));pid,fd=pty.fork()
            if pid==0:
                os.execve('/bin/sh',['sh','-c','cat '+shlex.quote(str(SOURCE/'scripts/install.sh'))+' | sh'],env)
            output=b'';sent=False;status=None;deadline=time.monotonic()+15
            try:
                while time.monotonic()<deadline:
                    if select.select([fd],[],[],0.1)[0]:
                        try:chunk=os.read(fd,65536)
                        except OSError:
                            _,status=os.waitpid(pid,0);break
                        if not chunk:
                            _,status=os.waitpid(pid,0);break
                        output+=chunk
                        if b'PRIVATE_KEY: ' in output and not sent:
                            os.write(fd,b'synthetic-key\n');sent=True
                    done,status_now=os.waitpid(pid,os.WNOHANG)
                    if done:status=status_now;break
                if status is None:
                    done,status_now=os.waitpid(pid,os.WNOHANG)
                    if done:status=status_now
                self.assertIsNotNone(status,'installer did not exit')
                self.assertEqual(os.waitstatus_to_exitcode(status),0,output.decode(errors='replace'))
                text=output.decode(errors='replace')
                self.assertLess(text.index('LICENSE_VALID'),text.index('RUNTIME_INSTALLED'))
                self.assertLess(text.index('RUNTIME_INSTALLED'),text.index('HARMONIKA_TUI_STARTED'))
                self.assertNotIn('synthetic-key',text,'private prompt echoed the key')
            finally:
                os.close(fd)
                if status is None:
                    import signal
                    os.kill(pid,signal.SIGTERM);os.waitpid(pid,0)  # Only this test's child.

    def test_no_terminal_installs_without_prompt_or_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            env=self.fixture(Path(temp))
            run=subprocess.run(['sh',str(SOURCE/'scripts/install.sh')],env=env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('RUNTIME_INSTALLED',run.stdout)
            self.assertNotIn('LICENSE_FIRST',run.stdout)
            self.assertNotIn('HARMONIKA_TUI_STARTED',run.stdout)

if __name__=='__main__':unittest.main()

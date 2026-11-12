import sys
from pathlib import Path
from unittest.mock import Mock,patch
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/tools'))
import desktop_browser as b


class BrowserTransportTests(unittest.TestCase):
    def test_permission_and_script_errors_are_not_misdiagnosed_as_account_faults(self):
        cases=[('Executing JavaScript through AppleScript is turned off','chrome_apple_events_javascript_disabled'),
               ('Not authorized to send Apple events (-1743)','macos_automation_permission_required'),
               ('SyntaxError: invalid regular expression JavaScript','browser_script_error')]
        for stderr,expected in cases:
            with patch.object(b.sys,'platform','darwin'),patch.object(b.subprocess,'run',return_value=Mock(returncode=1,stderr=stderr)):
                with self.assertRaisesRegex(b.BrowserError,expected):b.run_jxa('fixed',{})


if __name__=='__main__':unittest.main()

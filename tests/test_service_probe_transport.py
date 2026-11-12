import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('transport',ROOT/'runtime/tools/service_probe_transport.py')
transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)


class ServiceProbeTransportTests(unittest.TestCase):
    def test_quote_heavy_payload_survives_shell_and_retains_arguments(self):
        text="literal 'quotes' `$(echo should-not-run)` and $HOME\\n"*180
        script='import json,sys;print(json.dumps({"text":'+repr(text)+',"args":sys.argv[1:]}))'
        command=transport.build_probe_command(script,'zimbra.service',80)
        self.assertLessEqual(len(command),8192)
        r=subprocess.run(['/bin/sh','-c',command],capture_output=True,text=True,check=True)
        data=json.loads(r.stdout);self.assertEqual(data['text'],text);self.assertEqual(data['args'],['zimbra.service','80'])

    def test_actual_probe_fits_existing_ssh_command_limit(self):
        script=(ROOT/'runtime/tools/service_probe.py').read_text()
        self.assertLessEqual(len(transport.build_probe_command(script,'zimbra.service',80)),8192)

    def test_invalid_requests_fail_before_building_command(self):
        for service,lines in [('a.service;echo injected',80),('ssh.service',True),('x.service',201)]:
            with self.assertRaises(ValueError):transport.build_probe_command('pass',service,lines)


if __name__=='__main__':unittest.main()

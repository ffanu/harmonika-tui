import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]

class Activation(unittest.TestCase):
    def test_scoped_private_patch_and_idempotent_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=pathlib.Path(directory)
            prefix="VISION_TOKEN='synthetic-private'\nOUTPUT_DIR='/tmp'\ndef unrelated():\n    return 'preserved'\n"
            (owner/'mcp_server.py').write_text(prefix+'def tool_generate_fluxhd(args):\n    from visual_media import ensure_media_port, png_result\n    return png_result(None,None,None)\n')
            cmd=[sys.executable,str(ROOT/'scripts/activate-image-quality.py'),'--owner',directory]
            subprocess.run(cmd,check=True,capture_output=True)
            self.assertTrue((owner/'mcp_server.py').read_text().startswith(prefix))
            self.assertIn('return generate(args, OUTPUT_DIR, VISION_TOKEN)',(owner/'mcp_server.py').read_text())
            for name in ('image_generation.py','image_anatomy.py','visual_media.py','media_tunnels.py'):
                self.assertEqual((owner/name).read_bytes(),(ROOT/'runtime/tools'/name).read_bytes())
                self.assertEqual((owner/name).stat().st_mode&0o777,0o600)
            self.assertIn("'updated_files': 0",subprocess.run(cmd,check=True,capture_output=True,text=True).stdout)
            (owner/'image_anatomy.py').write_text('# outdated\n')
            self.assertIn("'updated_files': 1",subprocess.run(cmd,check=True,capture_output=True,text=True).stdout)

    def test_unknown_adapter_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            file=pathlib.Path(directory)/'mcp_server.py';data=b'def tool_generate_fluxhd(args):\n    return 123\n';file.write_bytes(data)
            out=subprocess.run([sys.executable,str(ROOT/'scripts/activate-image-quality.py'),'--owner',directory],capture_output=True)
            self.assertNotEqual(out.returncode,0);self.assertEqual(file.read_bytes(),data)

if __name__=='__main__':unittest.main()

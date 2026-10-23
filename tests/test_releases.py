import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

SOURCE=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('launcher',SOURCE/'runtime/harmonika.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
sha=lambda b:hashlib.sha256(b).hexdigest()

class Releases(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'versions').mkdir()
        js="const fs=require('fs'),c=require('crypto');const k=c.generateKeyPairSync('ed25519');fs.writeFileSync(process.argv[1],k.privateKey.export({type:'pkcs8',format:'pem'}));fs.writeFileSync(process.argv[2],k.publicKey.export({type:'spki',format:'pem'}));"
        subprocess.run([m.NODE,'-e',js,str(self.root/'private.pem'),str(self.root/'trusted-public.pem')],check=True)
        self.binary=b'#!/bin/sh\necho "harmonika version v0.98.1"\n'
        self.files={'tools/protocol-check.cjs':b'process.stdout.write("ok");','tools/helper.cjs':b'"use strict";'}
        directory=self.root/'versions/2026.10.24.1';directory.mkdir()
        for name,data in self.files.items():
            file=directory/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)
        (directory/'harmonika').write_bytes(self.binary);os.chmod(directory/'harmonika',0o755)
        (directory/'release.json').write_bytes(self.envelope(self.manifest('2026.10.24.1')))
        m.switch('2026.10.24.1',self.root)

    def manifest(self,release):return {'release':release,'binary_sha256':sha(self.binary),'files':{n:{'sha256':sha(b)} for n,b in self.files.items()}}
    def envelope(self,manifest):
        js="const fs=require('fs'),c=require('crypto');const b=fs.readFileSync(0);process.stdout.write(JSON.stringify({payload:b.toString('base64'),signature:c.sign(null,b,fs.readFileSync(process.argv[1])).toString('base64')}));"
        return subprocess.run([m.NODE,'-e',js,str(self.root/'private.pem')],input=json.dumps(manifest).encode(),capture_output=True,check=True).stdout
    def package(self,manifest=None,files=None):
        file=self.root/'update.zip'
        with zipfile.ZipFile(file,'w') as out:
            out.writestr('release.json',self.envelope(manifest or self.manifest('2026.10.24.2')))
            for name,data in (files or self.files).items():out.writestr(name,data)
        return file
    def unchanged(self):self.assertEqual(m.active(self.root)[1]['release'],'2026.10.24.1')

    def test_signed_install_and_real_rollback(self):
        result=m.install_package(self.package(),self.root)
        self.assertEqual(result['release'],'2026.10.24.2')
        self.assertEqual(m.active(self.root)[0].name,'2026.10.24.2')
        self.assertEqual(m.rollback(self.root)['release'],'2026.10.24.1')
    def test_reject_tampered_signature(self):
        envelope=json.loads(self.envelope(self.manifest('2026.10.24.2')));envelope['payload']=base64.b64encode(b'{}').decode()
        with self.assertRaisesRegex(ValueError,'signature'):m.verify_envelope(json.dumps(envelope).encode(),self.root)
        self.unchanged()
    def test_reject_tampered_file(self):
        with self.assertRaisesRegex(ValueError,'checksum'):m.install_package(self.package(files={**self.files,'tools/helper.cjs':b'evil'}),self.root)
        self.unchanged()
    def test_reject_traversal(self):
        with self.assertRaisesRegex(ValueError,'unsafe_package_path'):m.install_package(self.package(files={**self.files,'../outside':b'x'}),self.root)
        self.assertFalse((self.root/'outside').exists());self.unchanged()
    def test_reject_binary_upgrade(self):
        manifest=self.manifest('2026.10.24.2');manifest['binary_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'binary_change'):m.install_package(self.package(manifest),self.root)
        self.unchanged()
    def test_syntax_failure_does_not_activate_release(self):
        self.files['tools/helper.cjs']=b'const = ;'
        with self.assertRaisesRegex(ValueError,'javascript_preflight'):m.install_package(self.package(),self.root)
        self.unchanged()
    def test_fails_closed_on_modified_binary(self):
        current,_=m.active(self.root);(current/'harmonika').write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError,'binary_checksum'):m.active(self.root)
    def test_concurrent_install_refused(self):
        with m.installation_lock(self.root):
            with self.assertRaisesRegex(ValueError,'update_in_progress'):m.install_package(self.package(),self.root)
        self.unchanged()
    def test_redirects_never_leave_harmonika(self):
        with self.assertRaisesRegex(ValueError,'external_update'):m.SameOrigin().redirect_request(None,None,302,'',{},'https://github.com/not-authorized')

if __name__=='__main__':unittest.main()

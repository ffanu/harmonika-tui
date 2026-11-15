"""Scoped Linux image factory activation; run after copying and testing helpers."""
import datetime
import hashlib
import os
from pathlib import Path
import subprocess

p=Path('/opt/harmonika-ai/lib/rtx-image-router.js');original=p.read_bytes();source=original.decode()
statement="return require('./image-quality-router.cjs').wrap(render,{outputRoot,publicHost,logger,validatePNG});"
if statement in source:
    print('image_quality_factory_already_active');raise SystemExit(0)
if hashlib.sha256(original).hexdigest()!='cf40f6e69e9d0f23fedca667a2f8f7b017afbef909b2811d0552b6de05d12f55':
    raise SystemExit('image_factory_changed_review_required')
anchor='  return async function render(prompt, references=[]) {'
ending='  };\n}\n\nmodule.exports = {createRTXFirstRouter,validatePNG,crc32};'
if source.count(anchor)!=1 or source.count(ending)!=1:raise SystemExit('unknown_image_factory')
source=source.replace(anchor,'  const render = async function render(prompt, references=[]) {',1)
source=source.replace(ending,'  };\n  '+statement+'\n}\n\nmodule.exports = {createRTXFirstRouter,validatePNG,crc32};',1)
temporary=p.with_suffix('.quality-candidate.cjs');temporary.write_text(source);temporary.chmod(0o600)
try:
    subprocess.run(['node','--check',str(temporary)],check=True,capture_output=True)
    subprocess.run(['node','--check',str(p.parent/'image-quality-router.cjs')],check=True,capture_output=True)
    backup=Path('/root/harmonika-image-quality-backups')/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True,mode=0o700)
    if p.read_bytes()!=original:raise SystemExit('concurrent_edit')
    saved=backup/p.name;saved.write_bytes(original);saved.chmod(0o600)
    temporary.chmod(p.stat().st_mode&0o777);os.replace(temporary,p)
finally:
    temporary.unlink(missing_ok=True)
print({'image_factory_patched':True,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})

"""Incremental signed release; only the compact tool dispatcher changes."""
import importlib.util,json,shutil,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
 spec=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
r=load('release_runtime',ROOT/'runtime/harmonika.py');b=load('release_builder',ROOT/'runtime/package-release.py')
current,old=r.active();r.verify_files(current,old)
release='2026.11.01.1';dest=ROOT/'dist'/release;dest.mkdir(parents=True,exist_ok=False)
for name in old['files']:
 target=dest/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(current/name,target)
shutil.copy2(ROOT/'runtime/tools/compact-core-mcp.cjs',dest/'tools/compact-core-mcp.cjs')
files={name:{**meta,'sha256':r.digest(dest/name)} for name,meta in old['files'].items()}
changed=[name for name in files if files[name]['sha256']!=old['files'][name]['sha256']]
assert changed==['tools/compact-core-mcp.cjs'] or set(changed)=={'tools/compact-core-mcp.cjs'}
manifest=dict(old,release=release,files=files,automatic_update=False,release_notes=['Fix invalid_tools with Studio active: 30 tool definitions, limit32.','PDF/video analysis and transcription grouped under media; capabilities and legacy handlers retained.','Native binary, ASCII, branding, permissions, SSH, and active sessions unchanged.'])
(dest/'release.json').write_bytes(b.sign(manifest))
package=ROOT/'dist'/('harmonika-terminal-'+release+'.zip')
with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as z:
 for name in [*files,'release.json']:z.write(dest/name,name)
print(json.dumps({'release':release,'changed_files':changed,'binary_unchanged':manifest['binary_sha256']==old['binary_sha256'],'package_bytes':package.stat().st_size}))

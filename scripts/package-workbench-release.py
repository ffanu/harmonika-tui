"""Signed incremental owner release: keep every existing helper and native binary."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

SOURCE=Path(__file__).resolve().parents[1]


def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def main():
    release=sys.argv[1];launcher=module(SOURCE/'runtime/harmonika.py','workbench_launcher')
    builder=module(SOURCE/'runtime/package-release.py','workbench_release_builder')
    if not launcher.RELEASE_RE.fullmatch(release):raise ValueError('invalid_release')
    current,previous=launcher.active();launcher.verify_files(current,previous)
    destination=SOURCE/'dist'/release
    if destination.exists():raise ValueError('release_already_exists')
    artifacts=launcher.ROOT/'workbench-build/artifacts'
    extras={
        'tools/harmonika.py':SOURCE/'runtime/harmonika.py',
        **{'tools/'+name:SOURCE/'runtime/tools'/name for name in ('compact-core-mcp.cjs','speech-tools.cjs','video-mcp.cjs','video-owner-client.cjs')},
        'tools/workbench.py':SOURCE/'runtime/tools/workbench.py',
        'tools/workbench_model.py':SOURCE/'runtime/tools/workbench_model.py',
        'tools/workbench_ui.py':SOURCE/'runtime/tools/workbench_ui.py',
        'tools/workbench-tmux':artifacts/'workbench-tmux',
        'tools/workbench-runtime.json':artifacts/'workbench-runtime.json',
        'tools/workbench-thirdparty.txt':artifacts/'workbench-thirdparty.txt',
        'tools/docs/TUI-WORKBENCH.md':SOURCE/'docs/TUI-WORKBENCH.md',
    }
    with tempfile.TemporaryDirectory(prefix='harmonika-workbench-release-') as temporary:
        stage=Path(temporary)
        for name,entry in previous['files'].items():
            src=current/name
            if launcher.digest(src)!=entry['sha256']:raise ValueError('active_source_changed')
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
        for name,src in extras.items():
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
        files={str(p.relative_to(stage)):{'sha256':launcher.digest(p)} for p in stage.rglob('*') if p.is_file()}
        manifest=dict(previous,release=release,files=files,automatic_update=False,
                      release_notes=['Fix invalid_tools: full native LSP + MCP configuration fits the 32-tool gateway limit',
                                     'ASCII/brand dan SHA binary native tetap; sesi aktif tidak disentuh'])
        (stage/'release.json').write_bytes(builder.sign(manifest))
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(stage,destination)
    package=SOURCE/'dist'/('harmonika-terminal-'+release+'.zip')
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in [*files,'release.json']:archive.write(destination/name,name)
    print(json.dumps({'release':release,'package':str(package),'files':len(files),
                      'native_binary_unchanged':manifest.get('binary_sha256')==previous.get('binary_sha256')}))


if __name__=='__main__':main()

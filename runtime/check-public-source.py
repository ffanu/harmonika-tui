#!/usr/bin/python3
"""Offline Git-index/source review; prints locations, never matched secrets."""
import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
PATTERNS={
 'private_key':re.compile(r'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----'),
 'api_token':re.compile(r'\bhai_(?:live|internal)_[a-fA-F0-9]{40,}\b'),
 'telegram_bot_secret':re.compile(r'\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b'),
 'cloud_token':re.compile(r'\bcf(?:at|ut)_[A-Za-z0-9_-]{25,}\b'),
}
def review(files,root):
    findings=[]
    for relative in files:
        file=root/relative
        if file.is_symlink():findings.append({'file':str(relative),'code':'symlink_requires_review'});continue
        if not file.is_file():continue
        name=file.name.lower()
        if name in {'harmonika-api-key','harmonika-mcp-ssh','config-before.json'} or name.startswith('signing-private') or name.endswith(('.token','.db','.sqlite','.sqlite3','.orig')):
            findings.append({'file':str(relative),'code':'private_file'});continue
        if file.stat().st_size>2*1024*1024:findings.append({'file':str(relative),'code':'large_artifact_requires_review'});continue
        raw=file.read_bytes()
        media={'docs/assets/harmonika-demo.gif':'gif','docs/assets/harmonika-demo.mp4':'mp4','docs/assets/harmonika-terminal.png':'png'}
        kind=media.get(str(relative))
        if kind:
            try:
                manifest=json.loads((root/'docs/assets/manifest.json').read_text())
                valid=(kind=='gif' and raw.startswith((b'GIF87a',b'GIF89a'))) or (kind=='png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (kind=='mp4' and raw[4:8]==b'ftyp')
                if valid and manifest[file.name]['sha256']==hashlib.sha256(raw).hexdigest():continue
            except (OSError,ValueError,KeyError):pass
            findings.append({'file':str(relative),'code':'public_media_checksum_or_signature_failed'});continue
        if b'\0' in raw[:8192]:findings.append({'file':str(relative),'code':'binary_requires_separate_release_review'});continue
        for line_number,line in enumerate(raw.decode('utf8',errors='replace').splitlines(),1):
            for code,pattern in PATTERNS.items():
                if pattern.search(line):findings.append({'file':str(relative),'line':line_number,'code':code})
    return findings

def candidates(root):
    result=subprocess.run(['git','-C',str(root),'ls-files','-z'],capture_output=True)
    if result.returncode==0:return [Path(p.decode()) for p in result.stdout.split(b'\0') if p],'git-index'
    files=[]
    for folder in ['runtime','skills','tests']:
        files += [p.relative_to(root) for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [Path(name) for name in ['README.md','AGENTS.md','.gitignore','NOTICE.md','UPSTREAM-LICENSE.md'] if (root/name).exists()]
    return files,'source-allowlist'

if __name__=='__main__':
    files,scope=candidates(ROOT);findings=review(files,ROOT)
    print(json.dumps({'ok':not findings,'scope':scope,'files_checked':len(files),'findings':findings,'note':'Heuristic check; public install and entitlement readiness are separate checks.'},indent=2))
    sys.exit(1 if findings else 0)

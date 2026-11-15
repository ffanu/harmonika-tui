#!/usr/bin/env python3
"""Atomic owner MCP extension installation; never restart user TUI processes."""
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('tool_firewall.py', 'project_memory_mcp.py', 'master_cli.py')


def main():
    spec = importlib.util.spec_from_file_location('owner_patch', ROOT/'scripts/patch-owner-context.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = (Path.home()/'.config/harmonika-terminal').resolve()
    mirror = Path.home()/'Projects/harmonika-engine/mcp'
    roots = [owner] + ([mirror] if (mirror/'mcp_server.py').is_file() else [])
    planned = {}
    for directory in roots:
        mcp = directory/'mcp_server.py'
        planned[mcp] = module.patch(mcp.read_text()).encode()
        for name in MODULES:
            source = ROOT/'runtime/tools'/name
            compile(source.read_text(), name, 'exec')
            planned[directory/name] = source.read_bytes()
    # New unsigned operator overlay only; do not mutate signed release files.
    skill = owner/'current/skills/harmonika-project-context/SKILL.md'
    if skill.exists() and skill.read_bytes() != (ROOT/'skills/harmonika-project-context/SKILL.md').read_bytes():
        raise ValueError('operator_context_skill_changed')
    planned[skill] = (ROOT/'skills/harmonika-project-context/SKILL.md').read_bytes()
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in planned}
    backup = owner/'context-backups'/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup, 0o700)
    rows = []
    for index, path in enumerate(planned):
        saved = backup/(str(index)+'.backup')
        if path.exists():
            shutil.copyfile(path, saved)
            os.chmod(saved, 0o600)
        rows.append({'path': str(path), 'backup': saved.name if path.exists() else None, 'sha256': before[path]})
    manifest = backup/'manifest.json'
    manifest.write_text(json.dumps(rows, indent=2))
    os.chmod(manifest, 0o600)
    try:
        for path, data in planned.items():
            if (hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None) != before[path]:
                raise ValueError('owner_file_changed')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = path.with_name(path.name+'.context-new')
            tmp.write_bytes(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
    except Exception:
        for row in rows:
            path = Path(row['path'])
            if row['backup']:
                tmp = path.with_name(path.name+'.context-restore')
                shutil.copyfile(backup/row['backup'], tmp)
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            else:
                path.unlink(missing_ok=True)
        raise
    print(json.dumps({'status': 'installed_for_next_mcp_process', 'files': len(planned), 'backup': str(backup), 'user_tui_restarted': False}))


if __name__ == '__main__':
    main()

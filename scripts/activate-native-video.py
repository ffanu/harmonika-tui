#!/usr/bin/env python3
"""Restore the existing verified video adapter as a separate native MCP, atomically.

No binary changes, new credentials, inference, or user-process signals.
"""
import datetime
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile


def atomic(path, data):
    fd, name = tempfile.mkstemp(prefix='.video-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path)
    parser.add_argument('--public-key',type=Path)
    parser.add_argument('--previous-package',type=Path,
                        help='verify the currently installed signed companion before upgrading it')
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    root=(Path.home()/'.config/harmonika-terminal').resolve()
    config=root/'harmonika.json';before=config.read_bytes();value=json.loads(before)
    node=shutil.which('node')
    adapter=(root/'current/tools/video-mcp.cjs').resolve(strict=True)
    if not node or not adapter.is_file():
        raise ValueError('video_adapter_missing')
    base_entry={'type':'local','command':[node,str(adapter)],'enabled':True}
    selected=adapter;package_metadata=None;previous_entry=None
    if args.package:
        spec=importlib.util.spec_from_file_location('video_release',repo/'runtime/tools/tui_release.py')
        release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
        public=args.public_key or root/'trusted-public.pem'
        if args.previous_package:
            previous=release.read_verified_manifest(args.previous_package,public)
            if 'native-video.cjs' not in previous['files']:
                raise ValueError('invalid_previous_video_release')
            previous_root=root/'native-video-releases'/previous['version']
            if previous_root.is_symlink() or previous_root.parent.is_symlink():
                raise ValueError('unsafe_video_release_path')
            for name,meta in previous['files'].items():
                target=previous_root/name
                if target.is_symlink() or not target.resolve().is_relative_to(previous_root.resolve()) or hashlib.sha256(target.read_bytes()).hexdigest()!=meta['sha256']:
                    raise ValueError('previous_video_release_changed')
            previous_entry={'type':'local','command':[node,str(previous_root/'native-video.cjs')],'enabled':True}
        package_metadata=release.verify_release(args.package,public)
        if 'native-video.cjs' not in package_metadata['files']:
            raise ValueError('invalid_video_release')
        destination=root/'native-video-releases'/package_metadata['version']
        if destination.parent.is_symlink() or destination.is_symlink():
            raise ValueError('unsafe_video_release_path')
        if not destination.exists():
            destination.parent.mkdir(exist_ok=True,mode=0o700)
            release.extract_release(args.package,public,destination)
        manifest=release.read_verified_manifest(args.package,public)
        for name,meta in manifest['files'].items():
            target=destination/name
            if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()) or hashlib.sha256(target.read_bytes()).hexdigest()!=meta['sha256']:
                raise ValueError('installed_video_release_changed')
        selected=destination/'native-video.cjs'
    entry={'type':'local','command':[node,str(selected)],'enabled':True}
    mcp=value.setdefault('mcp',{})
    if not isinstance(mcp,dict):
        raise ValueError('invalid_mcp_config')
    if 'harmonika_video' in mcp and mcp['harmonika_video']!=entry and not (args.package and mcp['harmonika_video'] in [base_entry,previous_entry]):
        raise ValueError('video_mcp_already_configured_differently')
    mcp['harmonika_video']=entry
    skill_root=root/'native-video-skills'
    skills=value.setdefault('skills',[])
    if not isinstance(skills,list):
        raise ValueError('native_skills_list_required')
    if str(skill_root) not in skills:
        skills.append(str(skill_root))
    guidance=skill_root/'VIDEO-ROUTING.md'
    instructions=value.setdefault('instructions',[])
    if not isinstance(instructions,list):
        raise ValueError('native_instructions_list_required')
    if str(guidance) not in instructions:
        instructions.append(str(guidance))
    backup=root/'video-recovery-backups'/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(parents=True,mode=0o700)
    atomic(backup/'harmonika.json',before)
    skill=skill_root/'harmonika-video/SKILL.md';skill.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if skill.exists():
        atomic(backup/'video-SKILL.md',skill.read_bytes())
    atomic(skill,(repo/'skills/harmonika-video/SKILL.md').read_bytes())
    atomic(guidance,b'''Rendering prompts: use concise English preserving names, quoted text, duration and supplied reference; answer in the user language. Exact product appearance requires a reference. Video/vidio creation: discover the video tool across ALL namespaces. Native harmonika_video.video generates/animates/polls/downloads; harmonika.video_studio edits existing files. Read harmonika-video skill. Use capabilities, submit ONCE at requested duration, then poll the SAME job/request ID respecting poll_after_seconds. Without a wait tool, at most three status checks per turn, then report pending ID; never spin. Download only after completed. Queued/running/uncertain is not completed. Never substitute a prompt or shorter clip for an authorized available render. Native v2 has no legacy harmonika catalog command.\n''')
    if config.read_bytes()!=before:
        raise ValueError('owner_config_changed')
    atomic(config,json.dumps(value,indent=2,ensure_ascii=False).encode()+b'\n')
    assert {k:v for k,v in value.items() if k not in ['mcp','skills','instructions']}=={k:v for k,v in json.loads(before).items() if k not in ['mcp','skills','instructions']}
    print(json.dumps({'ok':True,'mcp':'harmonika_video','adapter_sha256':hashlib.sha256(selected.read_bytes()).hexdigest(),'signed_package':package_metadata is not None,'backup_id':backup.name,'existing_mcp_preserved':True,'user_processes_signalled':False}))


if __name__=='__main__':
    main()

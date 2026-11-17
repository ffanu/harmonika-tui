"""Portable agent profile wiring; no account, history or device copies."""
import importlib.util
import ast
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


def migrate_local_connectors(config):
    """Keep this device's existing owner connections; never import another host."""
    config=Path(config)
    target=config/'private-connectors.json'
    previous=read_object(target)
    source=Path.home()/'.config/harmonika-terminal/mcp_server.py'
    if not source.is_file() or source.is_symlink():return 0
    info=source.stat()
    if info.st_size>200000 or (os.name!='nt' and (info.st_uid!=os.getuid() or info.st_mode&0o022)):return 0
    try:tree=ast.parse(source.read_text(encoding='utf-8'))
    except (ValueError,OSError,SyntaxError):return 0
    count=0
    for node in tree.body:
        if not isinstance(node,ast.Assign) or len(node.targets)!=1 or not isinstance(node.targets[0],ast.Name):continue
        name=node.targets[0].id
        if name not in ('VISION_TOKEN','MEDIA_TOKEN','STUDIO_KEY'):continue
        value=node.value
        if isinstance(value,ast.Call) and len(value.args)==2:value=value.args[1]
        if not isinstance(value,ast.Constant) or not isinstance(value.value,str) or not 16<=len(value.value)<=512:continue
        key='HARMONIKA_'+name
        if key not in previous:previous[key]=value.value;count+=1
    if count:atomic(target,previous)
    return count


def read_object(file):
    if file.is_symlink():
        raise ValueError('unsafe_feature_configuration')
    if not file.exists():
        return {}
    value = json.loads(file.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('invalid_feature_configuration')
    return value


def migrate_local_targets(config):
    """Move this user's legacy target settings only within the same device."""
    config=Path(config)
    target=config/'network-targets.json'
    source=Path.home()/'.config/harmonika-terminal/network-targets.json'
    if target.exists() or target.is_symlink() or source==target or not source.is_file() or source.is_symlink():return False
    info=source.stat()
    if info.st_size>1048576 or os.name=='nt' or info.st_uid!=os.getuid() or info.st_mode&0o077:return False
    value=read_object(source)
    atomic(target,value)
    return True


def atomic(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    if file.is_symlink():
        raise ValueError('unsafe_feature_configuration')
    temp = file.with_name('.' + file.name + '.' + uuid4().hex)
    try:
        with open(temp, 'x', encoding='utf-8') as stream:
            os.chmod(temp, 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temp, file)
    finally:
        temp.unlink(missing_ok=True)


def _plugins(existing, additions):
    if not isinstance(existing, list):
        raise ValueError('invalid_feature_plugins')
    # Replace only earlier copies of these same Harmonika plugins. Preserve
    # every unrelated operator plugin and its options.
    markers = ('/control-releases/', '/context-capacity-872k', '/plugins/tui-control', '/plugins/context-capacity')
    result = []
    for entry in existing:
        source = entry.get('package', '') if isinstance(entry, dict) else entry
        if isinstance(source, str) and any(m in source.replace('\\', '/') for m in markers):
            continue
        if entry not in result:
            result.append(entry)
    return result + additions


def configure(root, config, python, node):
    root, config = Path(root), Path(config)
    tools = root / 'current' / 'tools'
    if not (tools / 'feature-manifest.json').is_file():
        return {'configured': False, 'reason': 'legacy_runtime'}
    profile = read_object(tools / 'feature-manifest.json')
    if profile.get('schema_version') != 1:
        raise ValueError('invalid_feature_manifest')
    home=Path.home()
    if root==home/'.config/harmonika-terminal' and config in (home/'.config/harmonika',home/'.config/harmonika-terminal'):
        migrate_local_connectors(config)
        migrate_local_targets(config)
    modern = read_object(config / 'opencode.json')
    skills = modern.get('skills', [])
    if isinstance(skills, dict):
        skills = list(skills.get('paths', [])) + list(skills.get('urls', []))
    if not isinstance(skills, list):
        raise ValueError('invalid_feature_skills')
    default_skills = str(root / 'current' / 'skills')
    modern['skills'] = list(dict.fromkeys([*skills, default_skills]))
    control = str(tools / 'plugins' / 'tui-control')
    context = str(tools / 'plugins' / 'context-capacity')
    modern['plugins'] = _plugins(modern.get('plugins', []), [{'package': control}, {'package': context}])
    mcp = modern.setdefault('mcp', {})
    if not isinstance(mcp, dict):
        raise ValueError('invalid_feature_mcp')
    # Native servers take precedence over migrated V1 entries. Remove only
    # managed names so a stale disabled entry cannot shadow the new profile.
    servers = mcp.get('servers', {})
    if not isinstance(servers, dict):
        raise ValueError('invalid_feature_mcp_servers')
    for name in ('harmonika_agent', 'harmonika_video', 'harmonika_ssh', *(['harmonika'] if 'harmonika' in mcp else [])):
        servers.pop(name, None)
    mcp.update({
        'harmonika_agent': {'type': 'local', 'command': [str(python), '-I', str(tools / 'mcp-python-entry.py')], 'enabled': True},
        'harmonika_video': {'type': 'local', 'command': [str(node), str(tools / 'video-control' / 'native-video.cjs')], 'enabled': True},
    })
    if (tools/'ssh-mcp.cjs').is_file():
        mcp['harmonika_ssh']={'type':'local','command':[str(node),str(tools/'ssh-mcp.cjs')],'enabled':True}
    instructions = modern.setdefault('instructions', [])
    if not isinstance(instructions, list):
        raise ValueError('invalid_feature_instructions')
    for source in (str(config / 'HARMONIKA.md'), str(tools / 'AGENT-INSTRUCTIONS.md')):
        if source not in instructions:
            instructions.append(source)
    cli = read_object(config / 'cli.json')
    cli['plugins'] = _plugins(cli.get('plugins', []), [{'package': control, 'options': {'python': str(python)}}])
    cli.setdefault('tabs', {'mode': 'off'})
    atomic(config / 'opencode.json', modern)
    atomic(config / 'cli.json', cli)
    return {'configured': True, 'profile': profile.get('profile'), 'skills': len(profile.get('skills', [])), 'mcp_servers': [name for name in ('harmonika','harmonika_agent','harmonika_video','harmonika_ssh') if name in mcp]}


def remove_managed_companions(root, config):
    """Rollback to a legacy release without leaving missing plugin imports."""
    tools = str(Path(root)/'current'/'tools').replace('\\','/')
    config = Path(config)
    native = read_object(config/'opencode.json')
    cli = read_object(config/'cli.json')
    for value in (native, cli):
        plugins = value.get('plugins', [])
        value['plugins'] = [p for p in plugins if not (
            isinstance(p, dict) and str(p.get('package','')).replace('\\','/') in
            (tools+'/plugins/tui-control',tools+'/plugins/context-capacity'))]
    mcp = native.get('mcp', {})
    for name in ('harmonika_agent','harmonika_video','harmonika_ssh'):
        entry=mcp.get(name,{})
        if any(tools in str(arg).replace('\\','/') for arg in entry.get('command',[])):
            mcp.pop(name,None)
    native['instructions']=[p for p in native.get('instructions',[]) if str(p).replace('\\','/')!=tools+'/AGENT-INSTRUCTIONS.md']
    atomic(config/'opencode.json',native)
    atomic(config/'cli.json',cli)


def setup(root, directory=None):
    root = Path(root)
    directory = Path(directory) if directory else root / 'current'
    script = directory / 'tools' / 'feature_setup.py'
    if not script.is_file():
        return {'status': 'legacy_runtime'}
    if os.environ.get('HARMONIKA_INSTALL_FEATURES') == '0':
        return {'status': 'core_only', 'reason': 'explicit_operator_choice'}
    spec = importlib.util.spec_from_file_location('harmonika_feature_setup', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ensure_features(root, directory)


def status(root, config):
    root, config = Path(root), Path(config)
    tools = root / 'current' / 'tools'
    manifest = read_object(tools / 'feature-manifest.json')
    if not manifest:
        return {'installed': False, 'status': 'legacy_runtime'}
    native = read_object(config / 'opencode.json')
    cli = read_object(config / 'cli.json')
    registered = native.get('skills', [])
    if isinstance(registered, dict):
        registered = registered.get('paths', [])
    skill_path = str(root / 'current' / 'skills')
    return {
        'installed': True, 'profile': manifest.get('profile'),
        'skill_count': len(manifest.get('skills', [])),
        'skills_registered': skill_path in registered,
        'verified_agent_entrypoint': (tools / 'agent-v32' / 'agent-v32.js').is_file(),
        'native_control_registered': any(isinstance(x, dict) and x.get('package') == str(tools / 'plugins' / 'tui-control') for x in cli.get('plugins', [])),
        'mcp_servers_registered': {name: native.get('mcp', {}).get(name, {}).get('enabled') is True for name in ('harmonika', 'harmonika_agent', 'harmonika_video', 'harmonika_ssh')},
        'dependencies': read_object(root / 'feature-setup-status.json'),
        'remote_services': 'readiness_and_account_access_required',
    }

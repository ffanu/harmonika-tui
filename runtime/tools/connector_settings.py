"""Private per-device settings; never part of a public release."""
import json
import os
from pathlib import Path
import stat


def load():
    config=Path(os.environ.get('HARMONIKA_GLOBAL_CONFIG') or os.environ.get('HARMONIKA_CONFIG_DIR') or str(Path.home()/'.config/harmonika'))
    target=config/'private-connectors.json'
    if not target.exists():return {}
    if target.is_symlink():raise ValueError('unsafe_connector_settings')
    info=target.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size>16384 or (os.name!='nt' and (info.st_mode&0o077 or info.st_uid!=os.getuid())):
        raise ValueError('unsafe_connector_settings')
    value=json.loads(target.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('invalid_connector_settings')
    allowed={'HARMONIKA_VISION_TOKEN','HARMONIKA_MEDIA_TOKEN','HARMONIKA_STUDIO_KEY','HARMONIKA_RTX_GATEWAY_TOKEN'}
    result={k:v for k,v in value.items() if k in allowed and isinstance(v,str) and 0<len(v)<=512 and '\n' not in v and '\r' not in v}
    for k,v in result.items():os.environ.setdefault(k,v)
    return result

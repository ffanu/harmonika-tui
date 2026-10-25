"""Shared document limits and project path validation."""
from pathlib import Path
import zipfile
MAX_SIZE = 20 * 1024 * 1024

def scoped(root_value, value):
    root, candidate = Path(root_value), Path(value)
    if not root.is_absolute() or not candidate.is_absolute():
        raise ValueError('Root and file paths must be absolute')
    for p in (root, candidate):
        if '..' in p.parts or any(part.startswith('.') for part in p.parts[1:]):
            raise ValueError('Parent traversal and protected dot-paths are blocked')
        for parent in [p, *p.parents]:
            system_alias = (str(parent), str(parent.resolve())) in {('/tmp','/private/tmp'),('/var','/private/var')}
            if parent.is_symlink() and not system_alias:
                raise ValueError('Symlink paths are blocked')
    root = root.resolve()
    candidate = candidate.resolve()
    if root in (Path('/'), Path.home()) or not root.is_dir():
        raise ValueError('Root must be an existing project directory')
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError('File must be inside the selected project root')
    return candidate

def validate_zip(file):
    if file.stat().st_size > MAX_SIZE:
        raise ValueError('Input exceeds 20 MiB')
    with zipfile.ZipFile(file) as z:
        if len(z.infolist()) > 10000 or sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
            raise ValueError('Expanded document exceeds safe limits')

def strings(value, label):
    if not isinstance(value, list) or len(value) > 1000 or any(not isinstance(x, str) or len(x) > 100000 for x in value):
        raise ValueError(label + ' must be a bounded list of strings')
    return value

"""Explicit operator installation of pinned Wan 2.2 files on C: only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

CONTROL = Path(__file__).resolve().parent
MODELS = Path(r'C:\HarmonikaVideo\wan22-models')
COMFY = Path(r'C:\Users\Kraken\harmonika-flux-worker\ComfyUI')


def rows():
    values = json.loads((CONTROL / 'models.json').read_text())
    for row in values:
        rel = PurePosixPath(row['relative'])
        if (rel.is_absolute() or '..' in rel.parts or '\\' in row['relative']
                or ':' in row['relative'] or rel.parts[0] not in {'diffusion_models', 'text_encoders', 'vae'}):
            raise ValueError('invalid_model_path')
        expected = f"https://huggingface.co/{row['repo']}/resolve/{row['revision']}/split_files/{row['relative']}"
        if row['repo'] != 'Comfy-Org/Wan-2.2' or row['url'] != expected:
            raise ValueError('untrusted_model_source')
    return values


def main():
    if os.name != 'nt':
        raise SystemExit('windows_only')
    import msvcrt
    MODELS.mkdir(parents=True, exist_ok=True)
    with (CONTROL / 'install.lock').open('a+b') as guard:
        guard.seek(0); guard.write(b'0'); guard.flush(); guard.seek(0)
        msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        values = rows()
        if shutil.disk_usage(MODELS).free < sum(r['bytes'] for r in values) + max(r['bytes'] for r in values) + 10*1024**3:
            raise OSError('model_disk_reserve_required')
        spec = importlib.util.spec_from_file_location('pinned_download', CONTROL / 'pinned_download.py')
        downloader = importlib.util.module_from_spec(spec); spec.loader.exec_module(downloader)
        downloader.ROOT = CONTROL
        downloader.STATUS = CONTROL / 'download-status.json'
        downloader.configure_network()
        verified = []
        for row in values:
            path = MODELS / row['relative']
            downloader.download(row['url'], path, row['bytes'], row['sha256'])
            verified.append({k: row[k] for k in ('relative', 'bytes', 'sha256')})
            receipt = {'manifest_sha256': hashlib.sha256((CONTROL/'models.json').read_bytes()).hexdigest(),
                       'verified_files': verified}
            temporary = CONTROL / '.models-receipt.tmp'; temporary.write_text(json.dumps(receipt))
            os.replace(temporary, CONTROL / 'models-receipt.json')
            print(json.dumps({'file_verified': row['relative'], 'bytes': row['bytes'], 'completed': len(verified)}), flush=True)
            for category in ('diffusion_models', 'text_encoders', 'vae'):
                target = MODELS / category
                if not target.is_dir():
                    continue
                link = COMFY / 'models' / category / 'harmonika-quality'
                if link.is_junction():
                    actual = os.readlink(link).removeprefix('\\\\?\\')
                    if os.path.normcase(actual) != os.path.normcase(str(target)):
                        raise ValueError('unexpected_model_link')
                elif link.exists():
                    raise ValueError('existing_model_directory_preserved')
                else:
                    subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                   check=True, capture_output=True, timeout=15)
        downloader.status('completed', files=len(verified), models_sha256_verified=True)


if __name__ == '__main__':
    main()

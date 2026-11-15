"""Stage the pinned Wan 1.3B runtime on NTFS, without changing its model vault.

Default offline copy; explicit --download recovers unreadable vault files from
the existing pinned manifest. Each file is hashed before publication.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time

CONTROL = Path(__file__).resolve().parent
REVISION = 'a4c0cc35033229072cb6d5a92952c3d51716837f'
CACHE = Path(r'C:\HarmonikaVideo\model-cache') / REVISION
SOURCE = Path(r'C:\HarmonikaVideo\wangp\app\ckpts')


def runtime_rows(manifest):
    # The pinned upstream preflight also checks CLIP and both VAE variants,
    # even for T2V. Omitting them triggers an offline download failure.
    rows = [r for r in manifest if r.get('repo') == 'DeepBeepMeep/Wan2.1'
            and r.get('revision') == REVISION]
    if len(rows) != 15:
        raise ValueError('cache_manifest_incomplete')
    return rows


def verified_copy(source, destination, expected_bytes, expected_hash):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = hashlib.sha256()
        with destination.open('rb') as stream:
            while block := stream.read(8 * 1024 * 1024):
                existing.update(block)
        if destination.stat().st_size != expected_bytes or existing.hexdigest() != expected_hash:
            raise ValueError('existing_cache_mismatch')
        return
    fd, name = tempfile.mkstemp(prefix='.model-', dir=destination.parent)
    try:
        digest = hashlib.sha256(); total = 0
        with os.fdopen(fd, 'wb') as outgoing, source.open('rb') as incoming:
            while block := incoming.read(8 * 1024 * 1024):
                outgoing.write(block); digest.update(block); total += len(block)
                if total > expected_bytes:
                    raise ValueError('cache_size_mismatch')
            outgoing.flush(); os.fsync(outgoing.fileno())
        if total != expected_bytes or digest.hexdigest() != expected_hash:
            raise ValueError('cache_hash_mismatch')
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def ready_cache(cache=CACHE, control=CONTROL):
    """Trust only the private copy receipt plus pinned inventory, never a partial copy."""
    manifest = control / 'models.json'
    rows = runtime_rows(json.loads(manifest.read_text()))
    receipt = json.loads((cache / 'receipt.json').read_text())
    expected = [{k: r[k] for k in ('file', 'bytes', 'sha256')} for r in rows]
    if (receipt.get('revision') != REVISION
            or receipt.get('manifest_sha256') != hashlib.sha256(manifest.read_bytes()).hexdigest()
            or receipt.get('verified_files') != expected):
        raise ValueError('cache_receipt_mismatch')
    if not all((cache / r['file']).is_file() and (cache / r['file']).stat().st_size == r['bytes'] for r in rows):
        raise ValueError('cache_inventory_mismatch')
    return cache


def stage(source=SOURCE, cache=CACHE, download=False):
    manifest = CONTROL / 'models.json'
    rows = runtime_rows(json.loads(manifest.read_text()))
    cache.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(cache).free < sum(r['bytes'] for r in rows) + 5 * 1024**3:
        raise OSError('cache_disk_space_low')
    downloader = None
    if download:
        spec = importlib.util.spec_from_file_location('pinned_model_download', CONTROL / 'download.py')
        downloader = importlib.util.module_from_spec(spec); spec.loader.exec_module(downloader)
        downloader.STATUS = cache / 'download-status.json'
    for row in rows:
        relative = PurePosixPath(row['file'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in row['file']:
            raise ValueError('invalid_model_path')
        start = time.monotonic()
        target = cache.joinpath(*relative.parts)
        if downloader is None:
            verified_copy(source.joinpath(*relative.parts), target, row['bytes'], row['sha256'])
        else:
            downloader.download(row['url'], target, row['bytes'], row['sha256'])
        print(json.dumps({'event': 'cache_file_verified', 'file': row['file'],
                          'bytes': row['bytes'], 'seconds': round(time.monotonic()-start, 2)}), flush=True)
    receipt = {'revision': REVISION, 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
               'verified_files': [{k: r[k] for k in ('file', 'bytes', 'sha256')} for r in rows]}
    temporary = cache / '.receipt.tmp'
    temporary.write_text(json.dumps(receipt, indent=2))
    os.replace(temporary, cache / 'receipt.json')
    print(json.dumps({'event': 'cache_ready', 'files': len(rows)}), flush=True)


if __name__ == '__main__':
    if os.name != 'nt':
        raise SystemExit('windows_only')
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    import msvcrt
    with (CONTROL / 'download.lock').open('a+b') as guard:
        guard.seek(0); guard.write(b'0'); guard.flush(); guard.seek(0)
        msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        stage(download=args.download)

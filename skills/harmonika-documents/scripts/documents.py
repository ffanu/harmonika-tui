#!/usr/bin/env python3
"""Project-scoped local document dispatcher. No network or implicit installs."""
import argparse
import importlib
import importlib.util
import json
import os
from io import BytesIO
import subprocess
import tempfile
from pathlib import Path
import sys

# Support both direct CLI execution and importlib-based callers/tests.
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from document_common import scoped, validate_zip, strings

MANIFEST = SCRIPTS.parent / 'manifest.yaml'
# JSON is a YAML 1.2 subset; load without introducing a YAML dependency.
REGISTRY_ERROR = False
try:
    REGISTRY = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if REGISTRY.get('schema_version') != 1 or not isinstance(REGISTRY.get('formats'), dict) or not REGISTRY['formats']:
        raise ValueError()
    FORMATS = REGISTRY['formats']
    MODULES = {ext: info['dependencies'][0] for ext, info in FORMATS.items()}
except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
    REGISTRY_ERROR = True
    REGISTRY, FORMATS, MODULES = {}, {}, {}

def available(module):
    return importlib.util.find_spec(module) is not None

def capabilities():
    if REGISTRY_ERROR:raise ValueError('invalid_document_manifest: Document registry is missing or invalid')
    """Keep legacy extension -> bool shape, add PDF truthfully."""
    return {ext: all(available(name) for name in item['dependencies'])
            for ext, item in FORMATS.items()}

def detailed_capabilities():
    formats = {}
    for ext, info in FORMATS.items():
        handler(ext)  # Validate signed handler imports even when optional engines are absent.
        operations = {action: all(available(name) for name in deps)
                      for action, deps in info['operations'].items()}
        formats[ext] = {'operations': operations,
                        'missing_dependencies': [name for name in info['dependencies'] if not available(name)],
                        'local': True}
    return {'schema_version': 1, 'formats': formats, 'reserved_formats': REGISTRY['reserved_formats'],
            'network': False, 'ocr': False, 'automatic_install': False}

def dependency(ext, action='create', data=None):
    if ext not in FORMATS:
        raise ValueError('unsupported_format: Supported formats: ' + ', '.join(FORMATS))
    deps = FORMATS[ext]['operations'].get(action)
    if deps is None:
        raise ValueError('unsupported_operation: ' + action + ' for ' + ext)
    # Preserve the old dependency-hook contract used by existing integrations.
    if action == 'create' and ext != '.pdf' and not capabilities()[ext]:
        raise ValueError('Unsupported: missing optional Python module ' + ', '.join(name for name in FORMATS[ext]['dependencies'] if not available(name)))
    missing = [name for name in deps if not available(name)]
    if ext == '.pdf' and action == 'edit' and data and 'watermark' in data and not available('reportlab'):
        missing.append('reportlab')
    if missing:
        raise ValueError('Unsupported: missing optional Python module ' + ', '.join(missing))

def handler(ext):
    return importlib.import_module(FORMATS[ext]['handler'])

def inspect(file):
    file = Path(file)
    ext = file.suffix.lower()
    dependency(ext, 'inspect')
    return handler(ext).inspect(file)

class BoundedBuffer(BytesIO):
    def __init__(self, maximum):
        super().__init__()
        self.maximum = maximum
    def write(self, value):
        if self.tell() + len(value) > self.maximum:
            raise ValueError('output_too_large: exceeds format byte limit')
        return super().write(value)

def _save(file, save, ext):
    if not file.parent.is_dir():
        raise ValueError('Output parent directory does not exist')
    if file.exists():
        raise ValueError('Output exists; choose a new filename')
    # Render before publishing: parser/render failure cannot leave a partial file.
    buffer = BoundedBuffer((10 if ext == '.pdf' else 20) * 1024 * 1024)
    save(buffer)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(prefix='harmonika-document-', suffix='.pending', dir=str(file.parent), delete=False) as handle:
            pending = Path(handle.name)
            handle.write(buffer.getvalue())
            handle.flush()
            os.fsync(handle.fileno())
        os.link(pending, file)  # Atomic publication; refuses even a racing existing destination.
    except FileExistsError:
        raise ValueError('Output exists; choose a new filename') from None
    finally:
        buffer.close()
        if pending is not None:
            pending.unlink(missing_ok=True)
    return {'created': str(file), 'bytes': file.stat().st_size, 'format': ext}

def create(file, data):
    file = Path(file)
    ext = file.suffix.lower()
    dependency(ext)
    if not isinstance(data, dict):
        raise ValueError('Creation JSON must be an object')
    result = handler(ext).build(data)
    output = _save(file, result.save, ext)
    if ext == '.pdf':output.update({'font': result.font, 'warnings': result.warnings})
    return output

def edit(file, output, data, root):
    file, output = Path(file), Path(output)
    ext = file.suffix.lower()
    dependency(ext, 'edit', data)
    if output.suffix.lower() != ext:
        raise ValueError('unsupported_conversion: edit output must have the same format')
    # PDF reference paths pass through the same project scope guard as CLI inputs.
    result = handler(ext).edit(file, data, root)
    output = _save(output, result.write, ext)
    output['removed_content'] = ['annotations', 'page_actions', 'forms', 'catalog_actions', 'attachment_catalog']
    return output

def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('capabilities').add_argument('--detailed', action='store_true')
    for command in ('inspect', 'create', 'edit'):
        p = sub.add_parser(command)
        p.add_argument('--root', required=True)
        if command in ('inspect', 'edit'):
            p.add_argument('--input', required=True)
        if command in ('create', 'edit'):
            p.add_argument('--output', required=True)
            p.add_argument('--data', required=True)
    return parser.parse_args(argv)

def main(argv=None):
    args = arguments(argv)
    if args.command == 'capabilities':
        return detailed_capabilities() if args.detailed else capabilities()
    if args.command == 'inspect':
        return inspect(scoped(args.root, args.input))
    output = scoped(args.root, args.output)
    source = scoped(args.root, args.data)
    if source.stat().st_size > 1024 * 1024:
        raise ValueError('Creation JSON exceeds 1 MiB')
    data = json.loads(source.read_text(encoding='utf-8'))
    if args.command == 'edit':
        return edit(scoped(args.root, args.input), output, data, args.root)
    return create(output, data)

def emit_error(error):
    if isinstance(error, json.JSONDecodeError):
        code, message = 'invalid_spec_json', 'Document specification must be valid JSON'
    elif isinstance(error, ValueError):
        message = str(error)
        prefix = message.split(':', 1)[0]
        code = prefix if prefix.isidentifier() and prefix.islower() else 'invalid_input'
        if message.startswith('Unsupported: missing optional'):code = 'missing_dependency'
    else:
        code, message = 'document_processing_failed', 'Document processing failed'
    print(json.dumps({'error': message, 'code': code, 'status': 'error'}, ensure_ascii=False), file=sys.stderr)

def cli():
    runtime_root = Path(os.environ.get('HARMONIKA_TERMINAL_HOME', str(Path.home()/'.config/harmonika-terminal'))) / 'document-runtime'
    runtime_python = runtime_root / 'bin/python'
    if runtime_python.is_file() and Path(sys.prefix).resolve() != runtime_root.resolve() and not all(capabilities().values()):
        os.execv(str(runtime_python), [str(runtime_python), '-I', str(Path(__file__).resolve()), *sys.argv[1:]])
    args = arguments()
    # Validate scope using the actual user HOME before the child receives a clean HOME.
    if args.command != 'capabilities':
        for name in ('input', 'output', 'data'):
            if getattr(args, name, None):scoped(args.root, getattr(args, name))
    if any(value.lower().endswith('.pdf') for value in sys.argv[1:]):
        try:
            with tempfile.TemporaryDirectory(prefix='harmonika-document-worker-') as home:
                env = {key:value for key,value in os.environ.items() if not key.startswith(('PYTHON', 'RL_'))}
                env['HOME'] = home  # Do not execute ~/.reportlab_settings or ~/.reportlab_mods.
                child = subprocess.run([sys.executable, '-I', str(SCRIPTS/'pdf_worker.py'), *sys.argv[1:]],
                                       capture_output=True, timeout=30, cwd=home, env=env)
            if child.returncode < 0:
                raise ValueError('document_resource_limit: PDF processing exceeded its resource budget')
            sys.stdout.buffer.write(child.stdout)
            sys.stderr.buffer.write(child.stderr)
            return child.returncode
        except subprocess.TimeoutExpired:
            raise ValueError('document_resource_limit: PDF processing exceeded its resource budget') from None
    print(json.dumps(main(), ensure_ascii=False, default=str, allow_nan=False))
    return 0

if __name__ == '__main__':
    try:
        sys.exit(cli())
    except Exception as error:
        emit_error(error)
        sys.exit(2)

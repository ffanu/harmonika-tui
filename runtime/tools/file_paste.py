"""Explicit clipboard paste: Finder file references become text, never image bytes.

Read only on a paste shortcut. No clipboard mutation, file upload, or path logs.
"""
import json
from pathlib import Path
import re
import shlex
import subprocess
import uuid
from urllib.parse import unquote, urlsplit

MAX_BYTES = 1024 * 1024
READ_JXA = r'''
ObjC.import('AppKit');
const pb = $.NSPasteboard.generalPasteboard;
const paths = [];
const items = pb.pasteboardItems;
if (items) for (let i = 0; i < Number(items.count); i++) {
  const raw = items.objectAtIndex(i).stringForType('public.file-url');
  if (raw && !raw.isNil()) {
    // Finder often copies file:///.file/id=... (an inode reference), not a path.
    const url = $.NSURL.URLWithString(raw);
    const resolved = url.filePathURL;
    paths.push(resolved && !resolved.isNil() ? ObjC.unwrap(resolved.path) : ObjC.unwrap(raw));
  }
}
if (!paths.length) {
  const legacy = pb.propertyListForType('NSFilenamesPboardType');
  if (legacy && !legacy.isNil()) {
    const values = ObjC.deepUnwrap(legacy);
    if (Array.isArray(values)) for (const value of values) paths.push(value);
  }
}
const text = paths.length ? null : pb.stringForType('public.utf8-plain-text');
JSON.stringify({paths: paths, text: text && !text.isNil() ? ObjC.unwrap(text) : null});
'''


def paste_text(value):
    if not isinstance(value, dict):
        raise ValueError('clipboard_invalid')
    paths = value.get('paths', [])
    if not isinstance(paths, list) or len(paths) > 100:
        raise ValueError('clipboard_invalid')
    if paths:
        result = []
        for raw in paths:
            if not isinstance(raw, str) or len(raw) > 16384:
                raise ValueError('clipboard_invalid')
            if raw.startswith('file:'):
                url = urlsplit(raw)
                if url.scheme != 'file' or url.netloc not in ('', 'localhost') or url.query or url.fragment:
                    raise ValueError('clipboard_invalid')
                raw = unquote(url.path, errors='strict')
            if not Path(raw).is_absolute() or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in raw):
                raise ValueError('clipboard_invalid')
            if not Path(raw).exists():
                raise ValueError('clipboard_file_missing')
            # Preserve Unicode/spaces and literal shell metacharacters as text.
            # Always quote: a bare leading '/' opens the native command menu.
            quoted = "'" + raw.replace("'", "'\"'\"'") + "'"
            if quoted not in result:
                result.append(quoted)
        text = ' '.join(result)
    else:
        text = value.get('text')
        if not isinstance(text, str) or not text:
            raise ValueError('clipboard_no_file_or_text')
        if any((ord(c) < 32 and c not in '\n\t\r') or 127 <= ord(c) <= 159 for c in text):
            raise ValueError('clipboard_invalid')
        text = text.replace('\r\n', '\n').replace('\r', '\n')
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('clipboard_too_large')
    return text


def read_clipboard_data():
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', READ_JXA],
                            capture_output=True, text=True, timeout=3)
    if result.returncode or len(result.stdout.encode()) > 2 * MAX_BYTES:
        raise ValueError('clipboard_unavailable')
    return json.loads(result.stdout)


def read_clipboard():
    return paste_text(read_clipboard_data())


def paste_into_pane(context, pane, reader=read_clipboard_data, run=subprocess.run):
    if not re.fullmatch(r'%[0-9]+', pane or ''):
        raise ValueError('clipboard_invalid_pane')
    base = [context['tmux'], '-L', context['socket']]
    def call(*args, **kwargs):
        return run([*base, *args], capture_output=True, timeout=4, **kwargs)
    # Do not paste into a shell, file panel, or a different window.
    probe = call('display-message', '-p', '-t', pane,
                 '#{window_name}|#{pane_index}|#{@harmonika_file_panel}|#{pane_current_command}')
    fields = probe.stdout.decode().strip().split('|')
    if probe.returncode or len(fields) != 4 or fields[:3] != ['Chat', '0', ''] or fields[3] != 'harmonika':
        raise ValueError('clipboard_chat_only')
    value = reader()
    if isinstance(value, str):value = {'text':value}
    text = paste_text(value)
    is_file = bool(value.get('paths'))
    name = 'harmonika-paste-' + uuid.uuid4().hex
    try:
        if call('load-buffer', '-b', name, '-', input=text.encode()).returncode:
            raise ValueError('clipboard_paste_failed')
        # Native PasteMsg auto-attaches real images. Finder paths contain no
        # control characters/newlines, so send them as literal key input instead.
        # Multiline clipboard text still needs bracketed paste to avoid submit.
        flags = [] if is_file else ['-p']
        if call('paste-buffer', *flags, '-d', '-b', name, '-t', pane).returncode:
            raise ValueError('clipboard_paste_failed')
    finally:
        call('delete-buffer', '-b', name)


#!/usr/bin/env python3
"""Harmonika Visual Engine: inline images, charts, gauges, tables, topology,
progress, markdown, toast — all rendered for terminal (ANSI + Unicode).

Actions (via `viz` MCP tool or CLI):
  image      — inline image in terminal (Kitty/iTerm2/Ghostty protocol) + ASCII fallback
  sparkline  — ▁▂▃▄▅▆▇█ trend line from data array
  barchart   — horizontal colored bars with labels
  gauge      — [██████░░░░] 62% colored zones (green/yellow/red)
  table      — pretty box table ┌─┬─┐ from headers+rows
  progress   — progress bar with percentage
  topology   — ASCII network topology tree from discovered devices
  markdown   — ANSI markdown renderer (headers/bold/code/tables/checkbox/mermaid-flow)
  toast      — terminal notification (OSC 9/777 + bell)
  banner     — styled banner text
"""
import base64
import json
import math
import os
import re
import struct
import sys
import time
import zlib

RESET = '\x1b[0m'
BOLD = '\x1b[1m'
DIM = '\x1b[2m'
RED = '\x1b[31m'
GREEN = '\x1b[32m'
YELLOW = '\x1b[33m'
BLUE = '\x1b[34m'
MAGENTA = '\x1b[35m'
CYAN = '\x1b[36m'
WHITE = '\x1b[37m'
GRAY = '\x1b[90m'

SPARK_CHARS = '▁▂▃▄▅▆▇█'
BLOCK_FULL = '█'
BLOCK_EMPTY = '░'
BLOCK_HALF = '▓'


def _plain(s):
    return re.sub(r'\x1b\[[0-9;]*m', '', s)


# ═══════════ SPARKLINE ═══════════

def sparkline(data, width=40, color=None, fill_history=None):
    """Render ▁▂▃▄▅▆▇█ from numeric data. Optionally pad history for stable width."""
    vals = [v for v in data if isinstance(v, (int, float))]
    if fill_history:
        vals = (fill_history + vals)[-width:]
    if not vals:
        return ''
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    steps = len(SPARK_CHARS) - 1
    out = ''.join(SPARK_CHARS[int((v - lo) / rng * steps)] for v in vals[-width:])
    trend = ''
    if len(vals) >= 2:
        d = vals[-1] - vals[-min(len(vals), 10)]
        trend = f' {GREEN}↑{_fmt(d)}{RESET}' if d > 0 else (f' {RED}↓{_fmt(abs(d))}{RESET}' if d < 0 else f' {GRAY}→{RESET}')
    label = f' {CYAN}{_fmt(vals[-1])}{RESET} {GRAY}(min {_fmt(lo)} max {_fmt(hi)}){RESET}'
    return (color or '') + out + RESET + trend + label


def _fmt(v):
    if isinstance(v, float) and abs(v) >= 100:
        return f'{v:.0f}'
    if isinstance(v, float):
        return f'{v:.1f}'
    return str(v)


# ═══════════ GAUGE ═══════════

def gauge(percent, label='', width=0, warn=60, crit=85):
    if not width:
        try:
            from render_engine import term_width
            width = max(20, min(40, (term_width(80) - 30) // 2))
        except Exception:
            width = 30
    """Colored percentage bar: [████████░░░░░░] 62.0% label"""
    try:
        pct = max(0.0, min(100.0, float(percent)))
    except (TypeError, ValueError):
        pct = 0.0
    filled = int(pct / 100 * width)
    c = GREEN if pct < warn else (YELLOW if pct < crit else RED)
    bar = c + BLOCK_FULL * filled + GRAY + BLOCK_EMPTY * (width - filled) + RESET
    pct_color = c if pct >= warn else WHITE
    lbl = f' {BOLD}{label}{RESET}' if label else ''
    return f'[{bar}{RESET}] {pct_color}{pct:.0f}%{RESET}{lbl}'


# ═══════════ BARCHART ═══════════

BAR_COLORS = [CYAN, MAGENTA, BLUE, GREEN, YELLOW, RED]

def barchart(labels, values, width=0, unit=''):
    if not width:
        try:
            from render_engine import term_width
            width = max(15, min(40, term_width(80) - 25))
        except Exception:
            width = 40
    """Horizontal bar chart: label ████████ 42"""
    if not values:
        return ''
    mx = max(abs(v) for v in values) or 1
    lines = []
    for i, (lb, v) in enumerate(zip(labels, values)):
        c = BAR_COLORS[i % len(BAR_COLORS)]
        blen = max(1, int(abs(v) / mx * width))
        bar = c + BLOCK_FULL * blen + RESET
        lines.append(f'  {WHITE}{str(lb)[:14]:<14}{RESET} {bar} {BOLD}{_fmt(v)}{RESET}{unit}')
    return '\n'.join(lines)


# ═══════════ TABLE ═══════════

def _dw(s):
    try:
        from render_engine import dw
        return dw(s)
    except Exception:
        return len(str(s))


def _padw(s, width):
    return str(s) + ' ' * max(0, width - _dw(s))


def _truncw(s, width):
    out = ''
    for ch in str(s):
        if _dw(out + ch) > width:
            break
        out += ch
    return out


def table(headers, rows, colors=None, max_width=0):
    """Box table ┌──┬──┐ — unicode-width aware (CJK/emoji) + adaptive terminal width."""
    if not max_width:
        try:
            from render_engine import term_width
            max_width = min(120, term_width(100))
        except Exception:
            max_width = 100

    def clean(v):
        s = _plain(str(v if v is not None else '')).replace('\n', ' ')
        return s

    hdr = [clean(h) for h in headers]
    rws = [[clean(c) for c in r] for r in rows]
    widths = [_dw(h) for h in hdr]
    for r in rws:
        for i, c in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], _dw(c))
    budget = max_width - len(hdr) * 3 - 2
    if sum(widths) > budget and budget > 12 * len(hdr):
        scale = budget / sum(widths)
        widths = [max(5, int(w * scale)) for w in widths]

    def sep(l, m, r):
        return GRAY + l + m.join('─' * (w + 2) for w in widths) + r + RESET

    def row(cells, color=''):
        parts = []
        for i, c in enumerate(cells[:len(widths)]):
            parts.append(' ' + _padw(_truncw(c, widths[i]), widths[i]) + ' ')
        return GRAY + '│' + RESET + (GRAY + '│' + RESET).join(color + p + RESET for p in parts) + GRAY + '│' + RESET

    out = [sep('┌', '┬', '┐')]
    out.append(row(hdr, BOLD + CYAN))
    out.append(sep('├', '┼', '┤'))
    for ri, r in enumerate(rws):
        col = (colors[ri] if colors and ri < len(colors) and colors[ri] else '')
        out.append(row(r, col))
    out.append(sep('└', '┴', '┘'))
    return '\n'.join(out)


# ═══════════ PROGRESS ═══════════

def progress(current, total, label='', width=32):
    if total <= 0:
        return gauge(100, label, width)
    return gauge(current / total * 100, f'{label} {current}/{total}', width)


# ═══════════ TOPOLOGY ═══════════

def topology(devices, gateway=''):
    """ASCII tree: gateway → devices grouped by type/vendor."""
    def icon(d):
        vendor = str(d.get('vendor', '')).lower()
        ports = d.get('open_ports') or []
        if 'mikrotik' in vendor or 8291 in ports:
            return '🛡' if False else YELLOW + '▓' + RESET, 'MikroTik Router'
        if d.get('hostname') and any(x in str(d.get('hostname', '')).lower() for x in ('nas', 'synology', 'truenas')):
            return MAGENTA + '▤' + RESET, 'NAS'
        if 3389 in ports:
            return CYAN + '▣' + RESET, 'Windows RDP'
        if 22 in ports or vendor in ('Supermicro',):
            return GREEN + '◈' + RESET, 'Server/SSH'
        if 5900 in ports:
            return BLUE + '▥' + RESET, 'VNC'
        return GRAY + '·' + RESET, 'device'

    lines = [f'{BOLD}{CYAN}NETWORK TOPOLOGY{RESET}']
    gw_ip = gateway or 'gateway'
    lines.append(f'  {YELLOW}▓{RESET} {BOLD}{gw_ip}{RESET} {GRAY}(gateway/router){RESET}')
    if not devices:
        lines.append(f'  {GRAY}└── no devices discovered{RESET}')
        return '\n'.join(lines)
    for i, d in enumerate(devices):
        last = i == len(devices) - 1
        branch = '└──' if last else '├──'
        ic, kind = icon(d)
        host = d.get('hostname') or d.get('ip', '?')
        extra = []
        if d.get('mac'):
            extra.append(str(d['mac'])[:17])
        if d.get('open_ports'):
            extra.append('ports:' + ','.join(map(str, d['open_ports'][:6])))
        detail = f' {GRAY}{" · ".join(extra)}{RESET}' if extra else ''
        lines.append(f'  {GRAY}{branch}{RESET} {ic} {WHITE}{host}{RESET} {DIM}{kind}{RESET}{detail}')
    return '\n'.join(lines)


# ═══════════ MARKDOWN ═══════════

def markdown(text, width=90):
    """ANSI markdown: headers, bold, italic, code, lists, checkboxes, hr, tables, ``` blocks."""
    lines_out = []
    in_code = False
    code_lang = ''

    def inline(s):
        s = re.sub(r'\*\*(.+?)\*\*', BOLD + r'\1' + RESET, s)
        s = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', CYAN + r'\1' + RESET, s)
        s = re.sub(r'`([^`]+?)`', YELLOW + r'`\1`' + RESET, s)
        s = re.sub(r'\[([^\]]+?)\]\(([^)]+?)\)', CYAN + r'\1' + RESET + GRAY + r' (\2)' + RESET, s)
        return s

    for line in str(text).split('\n'):
        if line.strip().startswith('```'):
            if not in_code:
                in_code = True
                code_lang = line.strip()[3:]
                lines_out.append(GRAY + f'─── code{(":" + code_lang) if code_lang else ""} ' + '─' * max(0, 40 - len(code_lang)) + RESET)
            else:
                in_code = False
                lines_out.append(GRAY + '─' * 48 + RESET)
            continue
        if in_code:
            lines_out.append(GREEN + line + RESET)
            continue
        stripped = line.strip()
        if not stripped:
            lines_out.append('')
            continue
        m = re.match(r'^(#{1,4})\s+(.*)', stripped)
        if m:
            level = len(m.group(1))
            txt = m.group(2)
            if level == 1:
                lines_out.append(BOLD + MAGENTA + txt.upper() + RESET)
                lines_out.append(MAGENTA + '═' * min(len(txt), width) + RESET)
            elif level == 2:
                lines_out.append(BOLD + CYAN + txt + RESET)
                lines_out.append(CYAN + '─' * min(len(txt), width) + RESET)
            else:
                lines_out.append(BOLD + YELLOW + ('  ' * (level - 3)) + '▸ ' + txt + RESET)
            continue
        if re.match(r'^(-{3,}|\*{3,})$', stripped):
            lines_out.append(GRAY + '─' * width + RESET)
            continue
        m = re.match(r'^\s*[-*+]\s+\[([ xX])\]\s+(.*)', line)
        if m:
            checked = m.group(1).lower() == 'x'
            box = GREEN + '☑' + RESET if checked else GRAY + '☐' + RESET
            txt = inline(m.group(2))
            lines_out.append(f'  {box} {txt if checked else txt}')
            continue
        m = re.match(r'^\s*[-*+]\s+(.*)', line)
        if m:
            lines_out.append(f'  {YELLOW}•{RESET} {inline(m.group(1))}')
            continue
        m = re.match(r'^\s*(\d+)\.\s+(.*)', line)
        if m:
            lines_out.append(f'  {CYAN}{m.group(1)}.{RESET} {inline(m.group(2))}')
            continue
        if stripped.startswith('|') and stripped.endswith('|'):
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if all(re.match(r'^:?-+:?$', c) for c in cells):
                continue
            lines_out.append(GRAY + '│ ' + RESET + (GRAY + ' │ ' + RESET).join(inline(c) for c in cells))
            continue
        m = re.match(r'^\s*(graph|flowchart)\s+\w+', stripped)
        if m or stripped.startswith('mermaid'):
            lines_out.append(CYAN + '  [diagram]' + RESET + GRAY + ' flow below' + RESET)
            continue
        if '-->' in stripped:
            parts = [p.strip() for p in re.split(r'-{1,2}>', stripped)]
            if len(parts) >= 2:
                lines_out.append('  ' + (GREEN + ' → ' + RESET).join(BOLD + p + RESET for p in parts))
                continue
        lines_out.append('  ' + inline(stripped))
    return '\n'.join(lines_out)


# ═══════════ TOAST ═══════════

def toast(title, message=''):
    """Terminal notification: OSC 9 (iTerm2/WT), OSC 777 (konsole/rxvt), + bell."""
    msg = f'{title}{" — " + message if message else ""}'
    seqs = [
        f'\x1b]9;{msg}\x07',
        f'\x1b]777;notify;{title};{message or title}\x07',
        '\x07',
    ]
    return ''.join(seqs)


# ═══════════ BANNER ═══════════

def banner(text, color=MAGENTA):
    bar = color + '▄' * (len(text) + 8) + RESET
    return f'{bar}\n{color}{BOLD}   {text}   {RESET}\n{bar}'


# ═══════════ INLINE IMAGE ═══════════

def _png_info(data):
    """Parse PNG IHDR: (width, height, bitdepth, colortype)."""
    if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    w, h = struct.unpack('>II', data[16:24])
    depth, ctype = data[24], data[25]
    return w, h, depth, ctype


def _decode_png_rgb(data, max_pixels=400000):
    """Minimal PNG decoder → list of RGB rows. Supports 8-bit RGB/RGBA/gray, non-interlaced."""
    info = _png_info(data)
    if not info:
        return None, None
    w, h, depth, ctype = info
    if depth != 8 or ctype not in (0, 2, 6) or w * h > max_pixels * 4:
        return None, None
    idat = b''
    i = 8
    while i < len(data):
        ln = struct.unpack('>I', data[i:i+4])[0]
        ctype_chunk = data[i+4:i+8]
        if ctype_chunk == b'IDAT':
            idat += data[i+8:i+8+ln]
        i += 12 + ln
    try:
        raw = zlib.decompress(idat)
    except Exception:
        return None, None
    bpp = {0: 1, 2: 3, 6: 4}[ctype]
    stride = w * bpp
    px = bytearray(w * h * 3)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        if pos >= len(raw):
            break
        f = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos+stride])
        pos += stride
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x-bpp]) & 0xff
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xff
        elif f == 3:
            for x in range(stride):
                a = line[x-bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xff
        elif f == 4:
            for x in range(stride):
                a = line[x-bpp] if x >= bpp else 0
                c = prev[x-bpp] if x >= bpp else 0
                b = prev[x]
                p = a + b - c
                pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xff
        for x in range(w):
            if ctype == 0:
                g = line[x]
                px[(y*w+x)*3:(y*w+x)*3+3] = bytes([g, g, g])
            else:
                o = x * bpp
                px[(y*w+x)*3:(y*w+x)*3+3] = line[o:o+3]
        prev = line
    return w, h, bytes(px)


ASCII_RAMP = ' .:-=+*#%@'
ANSI_256 = [16, 231] + list(range(232, 256))

def image_ascii(path, cols=64):
    """Grayscale ASCII art thumbnail from PNG (works in ANY terminal)."""
    try:
        data = open(path, 'rb').read()
    except Exception as e:
        return f'{RED}cannot read: {e}{RESET}'
    w, h, px = _decode_png_rgb(data)
    if not w:
        return f'{YELLOW}ASCII fallback needs 8-bit non-interlaced PNG; open file directly:{RESET} {path}'
    rows = max(8, int(cols * (h / w) * 0.45))
    out = []
    for ry in range(rows):
        line = []
        for rx in range(cols):
            sx, sy = int(rx * w / cols), int(ry * h / rows)
            o = (min(sy, h-1) * w + min(sx, w-1)) * 3
            lum = (px[o]*299 + px[o+1]*587 + px[o+2]*114) // 1000
            line.append(ASCII_RAMP[min(lum * len(ASCII_RAMP) // 256, len(ASCII_RAMP)-1)])
        out.append(''.join(line))
    return GRAY + '\n'.join(out) + RESET + f'\n{DIM}{w}x{h} → ascii {cols}w{RESET}'


def _term_support():
    tp = os.environ.get('TERM_PROGRAM', '')
    term = os.environ.get('TERM', '')
    if tp in ('iTerm.app', 'WezTerm', 'ghostty', 'vscode'):
        return 'iterm'
    if 'kitty' in term or tp == 'kitty':
        return 'kitty'
    return None


def image_inline(path, cols=64, fallback_ascii=True):
    """Inline image: Kitty APC or iTerm2 1337 protocol; ASCII art fallback."""
    info = {'path': path, 'size': 0}
    try:
        data = open(path, 'rb').read()
        info['size'] = len(data)
        info['png'] = _png_info(data)[0:2] if _png_info(data) else None
    except Exception as e:
        return {'ok': False, 'error': str(e), 'render': ''}
    support = _term_support()
    info['protocol'] = support
    if support == 'iterm':
        b64 = base64.b64encode(data).decode()
        esc = f'\x1b]1337;File=inline=1;size={len(data)};name={base64.b64encode(os.path.basename(path).encode()).decode()}:{b64}\x07'
        return {'ok': True, 'protocol': 'iterm1337', 'render': _plain(esc) if _force_plain else esc, 'info': info}
    if support == 'kitty':
        b64 = base64.b64encode(data).decode()
        chunk = 4096
        out = []
        m = 1 if len(b64) > chunk else 0
        first = f'\x1b_Gf=100,a=T,q=2' + (',m=1' if m else '') + ';'
        out.append(first + b64[:chunk] + '\x1b\\')
        s = b64[chunk:]
        while s:
            nxt = s[chunk:]
            out.append('\x1b_Gm=' + ('1' if nxt else '0') + ';' + s[:chunk] + '\x1b\\')
            s = nxt
        return {'ok': True, 'protocol': 'kitty-apc', 'render': ''.join(out), 'info': info}
    art = image_ascii(path, cols) if fallback_ascii else ''
    return {'ok': True, 'protocol': 'ascii', 'render': _plain(art) if _force_plain else art,
            'note': f'Terminal {_term_support_label()} lacks inline image protocol (run inside Ghostty/iTerm2/Kitty/WezTerm for full image).', 'info': info}


def _term_support_label():
    return os.environ.get('TERM_PROGRAM', 'unknown') + '/' + os.environ.get('TERM', '?')


# ═══════════ DISPATCH (MCP) ═══════════

def _dispatch(args):
    action = args.get('action', '')
    try:
        from render_engine import colors_ok
        _force_plain = not colors_ok()
    except Exception:
        _force_plain = False
    if action == 'image':
        r = image_inline(args.get('path', ''), int(args.get('cols', 64)))
        return r
    if action == 'sparkline':
        data = args.get('data') or []
        if isinstance(data, str):
            data = [float(x) for x in re.split(r'[,\s]+', data.strip()) if x]
        return {'ok': True, 'render': sparkline(data, int(args.get('width', 40))),
                'plain': _plain(sparkline(data, int(args.get('width', 40))))}
    if action == 'gauge':
        return {'ok': True, 'render': gauge(args.get('value', 0), args.get('label', ''), int(args.get('width', 30))),
                'plain': _plain(gauge(args.get('value', 0), args.get('label', ''), int(args.get('width', 30))))}
    if action == 'barchart':
        labels = args.get('labels') or []
        values = args.get('values') or []
        r = barchart(labels, values, int(args.get('width', 40)), args.get('unit', ''))
        return {'ok': True, 'render': _plain(r) if _force_plain else r, 'plain': _plain(r)}
    if action == 'table':
        r = table(args.get('headers') or [], args.get('rows') or [])
        return {'ok': True, 'render': _plain(r) if _force_plain else r, 'plain': _plain(r)}
    if action == 'progress':
        r = progress(args.get('current', 0), args.get('total', 100), args.get('label', ''))
        return {'ok': True, 'render': _plain(r) if _force_plain else r, 'plain': _plain(r)}
    if action == 'topology':
        r = topology(args.get('devices') or [], args.get('gateway', ''))
        return {'ok': True, 'render': _plain(r) if _force_plain else r, 'plain': _plain(r)}
    if action == 'markdown':
        r = markdown(args.get('text', ''), int(args.get('width', 90)))
        return {'ok': True, 'render': _plain(r) if _force_plain else r, 'plain': _plain(r)}
    if action == 'toast':
        return {'ok': True, 'render': toast(args.get('title', 'Harmonika'), args.get('message', ''))}
    if action == 'banner':
        r = banner(args.get('text', ''), {'red': RED, 'green': GREEN, 'blue': BLUE, 'cyan': CYAN, 'yellow': YELLOW, 'magenta': MAGENTA}.get(args.get('color', 'magenta'), MAGENTA))
        return {'ok': True, 'render': _plain(r) if _force_plain else r, 'plain': _plain(r)}
    return {'ok': False, 'error': f'unknown action: {action}. Use: image, sparkline, gauge, barchart, table, progress, topology, markdown, toast, banner'}


VIZ_TOOLS = {
    'viz': {
        'description': 'Visual renderer: sparkline (▁▂▃▄▅▆▇█ trends), gauge ([████░░] 62% colored), '
                       'barchart (horizontal bars), table (box tables ┌─┬─┐), progress bar, '
                       'topology (ASCII network tree), markdown (ANSI renderer), '
                       'image (inline Kitty/iTerm2 protocol + ASCII fallback), toast (notification), banner.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string', 'enum': ['image', 'sparkline', 'gauge', 'barchart', 'table',
                                                      'progress', 'topology', 'markdown', 'toast', 'banner']},
                'path': {'type': 'string', 'description': 'Image file path (action=image)'},
                'data': {'type': 'array', 'items': {'type': 'number'}, 'description': 'Numeric data (sparkline)'},
                'value': {'type': 'number', 'description': 'Percent value (gauge)'},
                'label': {'type': 'string'},
                'labels': {'type': 'array', 'items': {'type': 'string'}},
                'values': {'type': 'array', 'items': {'type': 'number'}},
                'headers': {'type': 'array', 'items': {'type': 'string'}},
                'rows': {'type': 'array', 'items': {'type': 'array'}},
                'devices': {'type': 'array', 'items': {'type': 'object'}, 'description': 'network_discover devices'},
                'text': {'type': 'string', 'description': 'markdown text'},
                'current': {'type': 'number'}, 'total': {'type': 'number'},
                'title': {'type': 'string'}, 'message': {'type': 'string'},
            },
            'required': ['action'],
        },
    },
}

VIZ_HANDLERS = {'viz': _dispatch}

#!/usr/bin/env python3
"""Harmonika Render Engine: production-grade terminal rendering.

1. Diff rendering  — Screen class: only rewrites changed lines, alt-screen buffer
2. Latency budget  — Spinner shows instantly, first content <300ms guaranteed
3. Typewriter      — optional streaming text reveal (AI typing feel)
4. Adaptive width  — COLUMNS detect, responsive wrap for tables/gauges/markdown
5. TTY detection   — piped/file/CI → colors+spinner off automatically (NO_COLOR, TERM=dumb)
6. Unicode width   — pure-python wcwidth: CJK/emoji don't break table alignment
7. Theme switch    — light/dark/auto via OSC 11 background query, COLORFGBG, macOS style
"""
import os
import re
import select
import shutil
import sys
import threading
import time

# ═══════════ 5. TTY DETECTION ═══════════

def is_tty(stream=None):
    s = stream or sys.stdout
    try:
        return s.isatty()
    except Exception:
        return False


def colors_ok(stream=None):
    if os.environ.get('NO_COLOR'):
        return False
    if os.environ.get('TERM', '') == 'dumb':
        return False
    if os.environ.get('CI'):
        return False
    return is_tty(stream)


# ═══════════ 4. ADAPTIVE WIDTH ═══════════

def term_width(fallback=80):
    try:
        cols = shutil.get_terminal_size((fallback, 24)).columns
        return max(40, cols or fallback)
    except Exception:
        return fallback


def wrap_text(text, width=None):
    width = width or term_width()
    out = []
    for para in str(text).split('\n'):
        if not para:
            out.append('')
            continue
        cur = ''
        for word in para.split(' '):
            cand = (cur + ' ' + word).strip()
            if dw(cand) > width and cur:
                out.append(cur)
                cur = word
            else:
                cur = cand
        out.append(cur)
    return '\n'.join(out)


# ═══════════ 6. UNICODE WIDTH (wcwidth) ═══════════

_ZERO_WIDE = [
    (0x0300, 0x036F), (0x1AB0, 0x1AFF), (0x1DC0, 0x1DFF), (0x20D0, 0x20FF),
    (0xFE00, 0xFE0F), (0xFE20, 0xFE2F), (0x200B, 0x200F), (0x200D, 0x200D),
    (0xFEFF, 0xFEFF), (0x0591, 0x05BD), (0x0610, 0x061A), (0x064B, 0x065F),
    (0x0E31, 0x0E31), (0x0E34, 0x0E3A), (0x0E47, 0x0E4E),
]
_WIDE = [
    (0x1100, 0x115F), (0x2E80, 0x303E), (0x3041, 0x33FF), (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF), (0xA000, 0xA4CF), (0xA960, 0xA97F), (0xAC00, 0xD7A3),
    (0xF900, 0xFAFF), (0xFE10, 0xFE19), (0xFE30, 0xFE6F), (0xFF00, 0xFF60),
    (0xFFE0, 0xFFE6), (0x1F300, 0x1F64F), (0x1F680, 0x1F6FF),
    (0x1F900, 0x1F9FF), (0x1FA70, 0x1FAFF), (0x20000, 0x2FFFD), (0x30000, 0x3FFFD),
]


def _char_width(cp):
    for a, b in _ZERO_WIDE:
        if a <= cp <= b:
            return 0
    for a, b in _WIDE:
        if a <= cp <= b:
            return 2
    if cp == 0:
        return 0
    return 1


def dw(s):
    """Display width of string (CJK/emoji = 2, combining = 0)."""
    total = 0
    for ch in str(s):
        total += _char_width(ord(ch))
    return total


def pad(s, width):
    """Pad string to display width, unicode-aware."""
    s = str(s)
    gap = width - dw(s)
    return s + ' ' * max(0, gap)


def truncate(s, width, ellipsis='…'):
    s = str(s)
    if dw(s) <= width:
        return s
    out = ''
    for ch in s:
        if dw(out + ch) > width - dw(ellipsis):
            break
        out += ch
    return out + ellipsis


# ═══════════ 7. THEME SWITCH ═══════════

RESET = '\x1b[0m'
PLAIN_MAP = {
    'red': '\x1b[31m', 'green': '\x1b[32m', 'yellow': '\x1b[33m', 'blue': '\x1b[34m',
    'magenta': '\x1b[35m', 'cyan': '\x1b[36m', 'white': '\x1b[37m', 'gray': '\x1b[90m',
    'bold': '\x1b[1m', 'dim': '\x1b[2m',
}
LIGHT_MAP = dict(PLAIN_MAP)
LIGHT_MAP.update({
    'red': '\x1b[91m', 'green': '\x1b[32m', 'yellow': '\x1b[93m', 'blue': '\x1b[94m',
    'magenta': '\x1b[95m', 'cyan': '\x1b[96m', 'white': '\x1b[30m', 'gray': '\x1b[90m',
})
_theme_cache = {'theme': None}


def _query_bg_osc11(timeout=0.15):
    if os.name == 'nt' or not is_tty():
        return None
    try:
        import termios
        import tty
        fd = sys.stdout.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        sys.stdout.write('\x1b]11;?\x07')
        sys.stdout.flush()
        r, _, _ = select.select([fd], [], [], timeout)
        data = b''
        if r:
            data = os.read(fd, 100)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        m = re.search(rb'rgb:([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})', data)
        if m:
            def h(x):
                return int(x[:2], 16)
            return h(m.group(1)), h(m.group(2)), h(m.group(3))
    except Exception:
        pass
    return None


def _query_macos_style():
    if sys.platform != 'darwin':return None
    try:
        import subprocess
        r = subprocess.run(['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                           capture_output=True, text=True, timeout=3, encoding='utf-8', errors='replace')
        if 'Dark' in r.stdout:
            return 'dark'
        return 'light'
    except Exception:
        return None


def detect_theme(force=False):
    """auto → dark|light|plain. Cached unless force."""
    if _theme_cache['theme'] and not force:
        return _theme_cache['theme']
    pref = os.environ.get('HARMONIKA_THEME', 'auto').lower()
    if pref in ('dark', 'light', 'plain'):
        _theme_cache['theme'] = pref
        return pref
    if not colors_ok():
        _theme_cache['theme'] = 'plain'
        return 'plain'
    rgb = _query_bg_osc11()
    if rgb:
        r, g, b = rgb
        lum = (r * 299 + g * 587 + b * 114) / 1000
        theme = 'light' if lum > 128 else 'dark'
    else:
        fgbg = os.environ.get('COLORFGBG', '')
        if fgbg and ';' in fgbg:
            bg = fgbg.split(';')[-1]
            theme = 'light' if bg in ('15', '7') else 'dark'
        else:
            mac = _query_macos_style()
            theme = mac or 'dark'
    _theme_cache['theme'] = theme
    return theme


def c(name):
    """Color by current theme; '' if plain."""
    t = detect_theme()
    if t == 'plain':
        return ''
    m = LIGHT_MAP if t == 'light' else PLAIN_MAP
    return m.get(name, '')


# ═══════════ 1. DIFF RENDERING (alt screen) ═══════════

class Screen:
    """Differential renderer: writes ONLY changed lines per frame.
    alt_screen: use \x1b[?1049h buffer (restores caller's screen on exit)."""

    def __init__(self, alt_screen=True, stream=None, width=None):
        self.stream = stream or sys.stdout
        self.alt = alt_screen and is_tty(self.stream)
        self.prev = None
        self.width = width or term_width()
        self.frames = 0
        self.bytes_written = 0
        self.active = False

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, *a):
        self.end()

    def begin(self):
        self.active = True
        if self.alt:
            self.stream.write('\x1b[?1049h\x1b[?25l')
            self.stream.flush()

    def end(self):
        if not self.active:
            return
        if self.alt:
            self.stream.write('\x1b[?25h\x1b[?1049l')
            self.stream.flush()
        else:
            self.stream.write('\n')
        self.active = False

    def update(self, lines):
        """Render frame; only changed lines get clear+write, others just cursor-down."""
        lines = [truncate(l, self.width - 1, ellipsis='') for l in lines]
        buf = []
        if self.prev is not None and self.prev:
            buf.append(f'\x1b[{len(self.prev)}A\r')
        n = max(len(lines), len(self.prev or []))
        for i in range(n):
            new_l = lines[i] if i < len(lines) else None
            old_l = self.prev[i] if (self.prev and i < len(self.prev)) else None
            if new_l is not None and new_l == old_l:
                if i < n - 1:
                    buf.append('\r\x1b[1B')
                continue
            buf.append('\r\x1b[2K')
            if new_l is not None:
                buf.append(new_l)
            if i < n - 1:
                buf.append('\n')
        frame = ''.join(buf)
        self.stream.write(frame)
        self.stream.flush()
        self.frames += 1
        self.bytes_written += len(frame)
        self.prev = lines

    def stats(self):
        total_cells = self.frames * max(len(self.prev or [1]), 1) * self.width
        saved = max(0, 1 - self.bytes_written / total_cells) if total_cells else 0
        return {'frames': self.frames, 'bytes': self.bytes_written,
                'full_redraw_bytes_estimate': total_cells, 'bandwidth_saved_pct': round(saved * 100, 1)}


# ═══════════ 2. SPINNER + LATENCY BUDGET ═══════════

SPIN_FRAMES = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'


class Spinner:
    """Shows activity within latency budget (<300ms to first paint).
    If work finishes before `min_delay`, spinner never flashes."""

    def __init__(self, label='working', stream=None, interval=0.08, min_delay=0.05):
        self.stream = stream or sys.stdout
        self.on = is_tty(self.stream) and colors_ok(self.stream)
        self.label = label
        self.interval = interval
        self.min_delay = min_delay
        self._stop = threading.Event()
        self._thread = None
        self._shown_at = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.stop()

    def _spin(self):
        i = 0
        start = time.time()
        while not self._stop.is_set():
            if time.time() - start >= self.min_delay:
                if self._shown_at is None:
                    self._shown_at = time.time()
                self.stream.write(f'\r\x1b[2K{c("cyan")}{SPIN_FRAMES[i % len(SPIN_FRAMES)]}{reset_()} {self.label}')
                self.stream.flush()
            self._stop.wait(self.interval)
            i += 1

    def start(self):
        if not self.on:
            return
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self.on and self._shown_at is not None:
            self.stream.write('\r\x1b[2K')
            self.stream.flush()


def reset_():
    return '\x1b[0m' if colors_ok() else ''


def with_latency_budget(fn, label='working', budget_ms=300):
    """Run fn; guarantee visible feedback <300ms (spinner), return (result, timings_ms)."""
    t0 = time.time()
    sp = Spinner(label)
    sp.start()
    try:
        result = fn()
    finally:
        sp.stop()
    elapsed = (time.time() - t0) * 1000
    return result, {'total_ms': round(elapsed, 1), 'budget_ms': budget_ms,
                    'within_budget': elapsed <= budget_ms,
                    'spinner_shown': sp._shown_at is not None,
                    'first_paint_ms': round((sp._shown_at - t0) * 1000, 1) if sp._shown_at else round(elapsed, 1)}


# ═══════════ 3. TYPEWRITER ═══════════

def typewriter(text, cps=80, stream=None, enabled=None):
    """Reveal text char-by-char (AI typing feel). No-op when piped."""
    stream = stream or sys.stdout
    if enabled is None:
        enabled = is_tty(stream)
    if not enabled:
        stream.write(str(text))
        stream.flush()
        return False
    delay = 1.0 / max(cps, 1)
    for ch in str(text):
        stream.write(ch)
        stream.flush()
        time.sleep(delay)
    return True


# ═══════════ MCP TOOL ═══════════

def _dispatch(args):
    action = args.get('action', 'info')
    if action == 'info':
        return {'ok': True, 'tty': is_tty(), 'colors': colors_ok(),
                'width': term_width(), 'theme': detect_theme(),
                'no_color_env': bool(os.environ.get('NO_COLOR')),
                'term': os.environ.get('TERM', ''),
                'ci': bool(os.environ.get('CI'))}
    if action == 'measure':
        text = args.get('text', '')
        return {'ok': True, 'text': text, 'chars': len(text), 'display_width': dw(text),
                'padded_20': pad(text, 20) + '|', 'theme': detect_theme(), 'width': term_width()}
    if action == 'wrap':
        r = wrap_text(args.get('text', ''), args.get('width'))
        return {'ok': True, 'wrapped': r}
    if action == 'theme':
        t = detect_theme(force=args.get('force', False))
        rgb = _query_bg_osc11() if args.get('query') else None
        return {'ok': True, 'theme': t, 'background_rgb': rgb,
                'map': 'light-friendly palette' if t == 'light' else 'dark palette' if t == 'dark' else 'plain (no color)',
                'note': 'set HARMONIKA_THEME=light|dark|plain untuk override'}
    if action == 'typewriter':
        r = typewriter(args.get('text', ''), args.get('cps', 80))
        return {'ok': True, 'effect_shown': r, 'note': 'efek terlihat di terminal (TTY), bukan piped'}
    if action == 'latency':
        delay = args.get('simulated_work_ms', 600) / 1000
        def work():
            time.sleep(delay)
            return 'hasil'
        res, t = with_latency_budget(work, args.get('label', 'memproses'))
        return {'ok': True, 'result': res, **t}
    if action == 'diff_demo':
        frames = args.get('frames') or [
            ['frame-1: alpha', 'frame-1: beta', 'frame-1: gamma'],
            ['frame-1: alpha', 'frame-2: CHANGED', 'frame-1: gamma'],
            ['frame-1: alpha', 'frame-2: CHANGED', 'frame-3: NEW LINE'],
        ]
        import io
        buf = io.StringIO()
        class FakeTty(io.StringIO):
            def isatty(self):
                return True
        sio = FakeTty()
        s = Screen(alt_screen=False, stream=sio, width=80)
        s.begin()
        for f in frames:
            s.update(f)
        s.end()
        out = sio.getvalue()
        return {'ok': True, 'output_repr': repr(out[:600]), 'stats': s.stats(),
                'note': 'bytes kecil = hanya baris berubah yang ditulis ulang'}
    return {'ok': False, 'error': f'unknown action: {action}. Use: info, measure, wrap, theme, typewriter, latency, diff_demo'}


RENDER_TOOLS = {
    'render': {
        'description': 'Render quality engine: info (tty/colors/width/theme detect), measure (unicode display width '
                       'emoji/CJK), wrap (adaptive), theme (light/dark/auto via OSC11+COLORFGBG+macOS), '
                       'typewriter (AI typing effect), latency (spinner <300ms first paint budget), '
                       'diff_demo (differential line renderer proof).',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string', 'enum': ['info', 'measure', 'wrap', 'theme', 'typewriter', 'latency', 'diff_demo']},
                'text': {'type': 'string'},
                'width': {'type': 'number'},
                'cps': {'type': 'number'},
                'query': {'type': 'boolean'},
                'force': {'type': 'boolean'},
                'label': {'type': 'string'},
                'simulated_work_ms': {'type': 'number'},
                'frames': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'string'}}},
            },
            'required': ['action'],
        },
    },
}

RENDER_HANDLERS = {'render': _dispatch}

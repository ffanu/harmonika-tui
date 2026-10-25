#!/usr/bin/env python3
"""Independent validation: is the chat wheel-down singleton drop a MiniTerm
emulator artifact or real app behavior?

Drives the real Harmonika binary over a PTY and renders the SAME raw byte
stream through both the smoke's MiniTerm model and the independent pyte
terminal emulator. If both emulators agree at every checkpoint, MiniTerm is
exonerated and the singleton wheel-down drop is app-side truth.
"""

import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import pyte

SPEC = importlib.util.spec_from_file_location("smoke", Path(__file__).resolve().parent / "smoke-native-interaction.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def build_session(gateway, base):
    config = base / "config"
    config.mkdir(mode=0o700)
    synthetic = {
        "models": {
            "large": {"model": "harmonika-ai", "provider": "harmonika"},
            "small": {"model": "harmonika-ai", "provider": "harmonika"},
        },
        "providers": {
            "harmonika": {
                "name": "Harmonika Ai",
                "base_url": "http://127.0.0.1:%d/v1" % gateway.port,
                "api_key": "fixture-token",
                "models": [{"id": "harmonika-ai", "name": "Harmonika Ai", "context_window": 32768, "max_tokens": 8192}],
            }
        },
        "mcp": {},
        "lsp": {},
        "options": {
            "disable_provider_auto_update": True,
            "disable_default_providers": True,
            "disable_metrics": True,
            "context_paths": [],
            "global_context_paths": [],
            "tui": {"mouse": True, "scrollbar": "always", "exit_banner": "compact"},
        },
        "permissions": {},
    }
    for name in ("harmonika.json", "crush.json"):
        (config / name).write_text(json.dumps(synthetic))
        os.chmod(config / name, 0o600)
    project = base / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Uji sintetis. Jangan pakai tool.")
    global_data = base / "global-data"
    global_data.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARMONIKA_", "CRUSH_"))}
    env.update(
        HARMONIKA_GLOBAL_CONFIG=str(config),
        HARMONIKA_GLOBAL_DATA=str(global_data),
        TERM="xterm-256color",
        COLORTERM="truecolor",
        LANG="C",
    )
    return smoke.TuiSession(env, str(project), ["--cwd", str(project), "--data-dir", str(base / "data")], 32, 100)


class TolerantScreen(pyte.Screen):
    """pyte crashes on private-prefixed SGR (CSI ? ... m); tolerate it."""

    def select_graphic_rendition(self, *args, **kwargs):
        kwargs.pop("private", None)
        return super().select_graphic_rendition(*args, **kwargs)


class DualTerm:
    """Feeds one raw stream into MiniTerm and pyte simultaneously."""

    def __init__(self, session, rows, cols):
        self.session = session
        self.mini = session.term
        self.rows, self.cols = rows, cols
        self._reset_pyte()
        self.mini_feed = self.mini.feed
        self.mini_resize = self.mini.resize
        session.term.feed = self._feed
        session.term.resize = self._resize
        self.rebuilds = 0

    def _reset_pyte(self):
        self.pyte_screen = TolerantScreen(self.cols, self.rows)
        self.pyte_stream = pyte.ByteStream(self.pyte_screen)

    def _feed(self, data):
        self.mini_feed(data)
        # pyte's parser rejects private-prefixed SGR (CSI ? ... m); strip it.
        # Never replay history: replaying region scrolls double-applies them.
        safe = re.sub(rb"\x1b\[\?[0-9;]*m", b"", data)
        try:
            self.pyte_stream.feed(safe)
        except Exception:
            self.rebuilds += 1

    def _resize(self, rows, cols):
        self.mini_resize(rows, cols)
        self.rows, self.cols = rows, cols
        self.pyte_screen.resize(rows, cols)

    def visible(self, which):
        if which == "miniterm":
            return self.mini.visible()
        return "\n".join(row.rstrip() for row in self.pyte_screen.display)

    def markers(self, which):
        return sorted({int(m) for m in smoke.MARKER_RE.findall(self.visible(which))})

    def agree(self):
        return self.visible("miniterm") == self.visible("pyte")


def main():
    base = Path(tempfile.mkdtemp(prefix="harmonika-wheel-pyte-")).resolve()
    gateway = smoke.GatewayFixture()
    gateway.start()
    session = None
    result = {"product": "Harmonika Terminal", "tool": "pyte cross-emulator wheel validation", "checkpoints": []}
    try:
        session = build_session(gateway, base)
        dual = DualTerm(session, 32, 100)
        session.pump(6.0)
        if not gateway.requests:
            session.send("tampilkan fixture kode")
            session.pump(0.3)
            session.send("\r")
        session.wait_until(lambda: "HKSMOKE-END" in session.term.visible(), 40.0)
        session.pump(1.5)
        if not gateway.requests:
            result["error"] = "fixture gateway never received a request; isolation failed"
            print(json.dumps(result))
            return 2

        def checkpoint(label):
            row = {
                "label": label,
                "emulators_agree_full_text": dual.agree(),
                "emulators_agree_markers": dual.markers("miniterm") == dual.markers("pyte"),
                "miniterm_top_marker": (dual.markers("miniterm") or [None])[0],
                "pyte_top_marker": (dual.markers("pyte") or [None])[0],
            }
            result["checkpoints"].append(row)
            return row

        smoke.wheel(session, "down", 6, spacing=0.006)
        session.pump(0.5)
        checkpoint("bottom_after_down_burst")

        for i in range(2):
            session.send(smoke.sgr(64, 30, 12))
            session.pump(0.3)
            checkpoint("single_wheel_up_tick_%d" % (i + 1))

        top_before_down = dual.markers("miniterm")[:1]
        for i in range(3):
            session.send(smoke.sgr(65, 30, 12))
            session.pump(0.35)
            checkpoint("single_wheel_down_tick_%d" % (i + 1))
        top_after_down = dual.markers("miniterm")[:1]

        for _ in range(16):
            session.send(smoke.sgr(65, 30, 12))
            time.sleep(0.005)
        session.pump(0.9)
        checkpoint("coalesced_wheel_down_burst_16")

        markers_agree = all(c["emulators_agree_markers"] for c in result["checkpoints"])
        tops = [c["miniterm_top_marker"] for c in result["checkpoints"]]
        up_deltas = [b - a for a, b in zip(tops, tops[1:])]
        down_rows = [c["miniterm_top_marker"] for c in result["checkpoints"] if c["label"].startswith("single_wheel_down")]
        down_deltas = [b - a for a, b in zip(down_rows, down_rows[1:])]
        # +1 per tick proves movement; 0 is valid only when the view is at the
        # list bottom (last code marker + END visible) and cannot scroll on.
        at_bottom = lambda: (dual.markers("miniterm") or [0])[-1] >= smoke.CODE_LINES - 1 and smoke.MARK + "-END" in dual.visible("miniterm")
        single_down_moves = bool(down_deltas) and down_deltas[0] > 0 and all(d > 0 or (d == 0 and at_bottom()) for d in down_deltas)
        result["summary"] = {
            "emulators_agree_markers_every_checkpoint": markers_agree,
            "pyte_rebuilds": dual.rebuilds,
            "top_marker_trace": tops,
            "single_wheel_down_deltas": down_deltas,
            "single_wheel_down_moves": single_down_moves,
            "verdict": (
                "MiniTerm and pyte agree on every checkpoint; singleton wheel-down scrolls +1 line per tick in both"
                if markers_agree and single_down_moves and dual.rebuilds == 0
                else "emulators disagree or down ticks did not move; investigate harness"
            ),
        }
        print(json.dumps(result, indent=2))
        return 0 if markers_agree and single_down_moves and dual.rebuilds == 0 else 1
    finally:
        if session is not None and session.exit_code is None:
            session.terminate()
        gateway.stop()
        import shutil

        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

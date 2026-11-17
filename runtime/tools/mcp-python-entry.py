"""Load verified sibling helpers and speak UTF-8 JSON-RPC on every OS."""
from pathlib import Path
import runpy
import sys

for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
runpy.run_module('mcp_server', run_name='__main__')

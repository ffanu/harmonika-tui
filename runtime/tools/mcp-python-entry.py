"""Load only verified sibling helpers, including when Python uses -I."""
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
runpy.run_module('mcp_server', run_name='__main__')

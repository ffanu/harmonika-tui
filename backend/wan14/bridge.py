"""Reuse the verified transport without touching its running service or ledger."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent/('harmonika-high-video' if __import__('os').name == 'nt' else 'high-video')
if not BASE.is_dir():
    BASE = ROOT.parent/'high_video'


def load(name, path):
    spec = importlib.util.spec_from_file_location('wan14_'+name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

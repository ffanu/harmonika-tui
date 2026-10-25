"""Small known-good full-stack reference; not a claim of arbitrary app generation."""
from pathlib import Path
ROOT=Path(__file__).with_name('starter_template')
def writes():
 return [{'path':str(p.relative_to(ROOT)),'content':p.read_text()} for p in sorted(ROOT.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']

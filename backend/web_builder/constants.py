"""Portable contract constants, without runtime/crypto dependencies."""
import base64
from .common import require,safe_path,BuilderError
MAX_FILES=256;MAX_BYTES=16*1024*1024;MAX_FILE=512*1024;TENANT_BYTES=128*1024*1024
EXEC_IDS={'db.run.migration','db.run.seed','test.run.unit','test.run.integration','test.run.e2e','test.coverage.report','test.lint.run','test.type.check','test.performance'}
READERS={'spec.read.markdown','spec.read.txt','spec.read.json','spec.read.obsidian','spec.read.voice','spec.read.notation'}
def snapshot_check(files):
 require(type(files) is dict and len(files)<=MAX_FILES,'file_count_limit',413);total=0
 for p,b in files.items():
  safe_path(p);require(type(b) is str,'invalid_file')
  try:raw=base64.b64decode(b,validate=True)
  except Exception:raise BuilderError('invalid_file_encoding') from None
  total+=len(raw);require(len(raw)<=MAX_FILE and total<=MAX_BYTES,'storage_limit',413)
 return files

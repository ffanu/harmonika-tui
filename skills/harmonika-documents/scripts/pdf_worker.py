"""Internal isolated PDF worker; public callers use documents.py for deadlines."""
import sys
from pathlib import Path
import resource

resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
if sys.platform.startswith('linux'):
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import json
from documents import main, emit_error

if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False, default=str, allow_nan=False))
    except Exception as error:
        emit_error(error)
        sys.exit(2)

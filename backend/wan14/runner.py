"""One owned candidate render; shared process containment remains in the broker."""
import json
import sys
from pathlib import Path
import importlib.util

_spec = importlib.util.spec_from_file_location('wan14_runner_bridge', Path(__file__).with_name('bridge.py'))
_bridge = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_bridge)
load, BASE, ROOT = _bridge.load, _bridge.BASE, _bridge.ROOT

if __name__ == '__main__':
    module = load('runner', BASE/'runner.py'); module.CONTROL = ROOT
    module.wf = load('workflow', ROOT/'guided_workflow.py')
    job = Path(sys.argv[1])
    if job.parent != module.JOBS or len(job.name) != 32:
        raise SystemExit(2)
    try:
        result = module.run(job)
    except Exception as error:
        result = {'status': 'failed', 'error_code': str(error) if isinstance(error, module.wf.Failure) else 'quality_runner_failed'}
    (job/'result.json').write_text(json.dumps(result))
    raise SystemExit(0 if result['status'] == 'completed' else 1)

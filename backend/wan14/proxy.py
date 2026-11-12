from pathlib import Path
import importlib.util

base_path = Path('/opt/harmonika-ai/high-video/proxy.py')
if not base_path.exists():
    base_path = Path(__file__).resolve().parents[1]/'high_video/proxy.py'
spec = importlib.util.spec_from_file_location('wan14_proxy_base', base_path)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.BASE = 'http://127.0.0.1:18268/rpc'
module.ENABLED = Path('/etc/harmonika-ai/wan14-video.enabled')
route = module.route

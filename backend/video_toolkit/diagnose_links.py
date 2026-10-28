"""Mechanical linker diagnosis; no credentials or customer data."""
import importlib.util,json,traceback
from pathlib import Path
root=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('linker',root/'link_models.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
try:m.main()
except Exception as exc:
 frames=traceback.extract_tb(exc.__traceback__);last=frames[-1]
 print(json.dumps({'error_type':type(exc).__name__,'function':last.name,'line':last.lineno,'code':getattr(exc,'winerror',None),'message':str(exc)[:300]}))

"""Patch only the known Go upstream-error boundary in an isolated stage."""
import hashlib,json,sys
from pathlib import Path
stage=Path(sys.argv[1]);target=stage/'public_ai_internal.go';source=target.read_text();needle='\t\tswitch e.Error.Code {\n'
if source.count(needle)!=1:raise RuntimeError('gateway_boundary_changed')
replacement='\t\tif status, known := publicToolPolicyStatus(e.Error.Code); known {\n\t\t\tfail(status, e.Error.Code)\n\t\t\treturn\n\t\t}\n'+needle
old=hashlib.sha256(target.read_bytes()).hexdigest();target.write_text(source.replace(needle,replacement));print(json.dumps({'file':target.name,'before':old,'after':hashlib.sha256(target.read_bytes()).hexdigest()}))

"""Wait for this installation only, then run a bounded synthetic qualification."""
import json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
started=time.monotonic()
while time.monotonic()-started<12*3600:
 try:
  boot=json.loads((ROOT/'bootstrap-status.json').read_text());models=json.loads((ROOT/'model-download-status.json').read_text())
  if boot.get('status')=='failed' or models.get('phase')=='failed':
   (ROOT/'coordinator-status.json').write_text(json.dumps({'phase':'waiting_for_install_correction','raw_input_logged':False}));time.sleep(15);continue
  if boot.get('status')=='environment_installed' and models.get('phase')=='completed':
   result=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-I','-u',str(ROOT/'qualify_runtime.py')])
   raise SystemExit(result.returncode)
 except (OSError,ValueError):pass
 (ROOT/'coordinator-status.json').write_text(json.dumps({'phase':'waiting_for_verified_installation','raw_input_logged':False}))
 time.sleep(15)
(ROOT/'coordinator-status.json').write_text(json.dumps({'phase':'installation_wait_timeout','raw_input_logged':False}))
raise SystemExit(1)

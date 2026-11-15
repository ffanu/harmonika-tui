"""Finite registered fixture writer. Natural release; no process signals."""
import json,sqlite3,sys,time,os
from pathlib import Path
token=sys.argv[1];assert len(token)==16 and all(x in '0123456789abcdef' for x in token)
root=Path('/data');p=root/('lock-'+token+'.json');release=root/('lock-'+token+'.release');c=sqlite3.connect(root/'app.sqlite',timeout=.3)
c.execute('BEGIN IMMEDIATE');started=time.time();p.write_text(json.dumps({'state':'holding','started_at':started,'pid':os.getpid()}));deadline=time.monotonic()+45
try:
 while time.monotonic()<deadline and not release.exists():time.sleep(.025)
finally:c.rollback();c.close();p.write_text(json.dumps({'state':'released','started_at':started,'finished_at':time.time()}))

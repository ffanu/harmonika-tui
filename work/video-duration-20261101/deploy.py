import sys,json
from pathlib import Path
sys.path.insert(0,'/Users/id/Projects/hai-studio-20261101');from remote import connect
base=Path('/Users/id/Projects/harmonika-tui')
with connect() as c:
 with c.open_sftp() as f:
  try:f.mkdir('/opt/harmonika-ai/flex-video')
  except OSError:pass
  for name in ('engine.py','server.py'):
   f.put(str(base/'backend/flex_video'/name),'/opt/harmonika-ai/flex-video/'+name)
  service='''[Unit]
Description=Harmonika flexible video duration
After=network.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 -I /opt/harmonika-ai/flex-video/server.py
WorkingDirectory=/opt/harmonika-ai/flex-video
Environment=PYTHONPATH=/opt/harmonika-ai/flex-video
UMask=0077
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/harmonika-flex-video
MemoryMax=1G
[Install]
WantedBy=multi-user.target
'''
  # -I suppresses script directory imports: use trusted explicit module directory.
  p=base/'backend/flex_video/server.py';s=p.read_text().replace('from engine import Engine,Failure,IDS','import sys\nsys.path.insert(0,str(Path(__file__).resolve().parent))\nfrom engine import Engine,Failure,IDS')
  with f.open('/opt/harmonika-ai/flex-video/server.py','w') as o:o.write(s)
  with f.open('/etc/systemd/system/harmonika-flex-video.service','w') as o:o.write(service)
 command="install -d -m 700 /var/lib/harmonika-flex-video && python3 -m py_compile /opt/harmonika-ai/flex-video/*.py && systemctl daemon-reload && systemctl enable --now harmonika-flex-video.service && systemctl is-active harmonika-flex-video.service"
 i,o,e=c.exec_command(command,timeout=20);i.channel.shutdown_write();print(o.read().decode());print(e.read().decode());assert o.channel.recv_exit_status()==0

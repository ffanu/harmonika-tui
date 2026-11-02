"""Activate only the duration extension; preserve all current live integrations."""
import sys,hashlib,json
from pathlib import Path
sys.path.insert(0,'/Users/id/Projects/hai-studio-20261101');from remote import connect
root=Path('/Users/id/Projects/harmonika-tui')
with connect() as c:
 with c.open_sftp() as f:
  old=f.open('/opt/harmonika-ai/video-routing/facade.py').read().decode()
  (root/'work/video-duration-20261101/facade.baseline.py').write_text(old)
  code=old.replace("'download_video':{'job_id'}","'download_video':{'job_id','offset'}")
  needle=' # Only signed owners reach this extension.'
  insertion=""" # Flexible-duration extension, after owner authorization and field validation.
 flex_path=ROOT/'flex_video_proxy.py'
 if flex_path.is_file():
  spec=importlib.util.spec_from_file_location('harmonika_flex_video',flex_path);flex=importlib.util.module_from_spec(spec);spec.loader.exec_module(flex)
  routed=flex.route(data,owner)
  if routed is not None:return routed
"""
  assert needle in code and 'flex_video_proxy.py' not in code
  code=code.replace(needle,insertion+needle)
  code=code.replace("'image_to_video_duration_seconds':[1]","'image_to_video_duration_seconds':[1,10],'image_to_video_duration_range':{'minimum':1,'mode':'segmented','resource_limited':True},'flexible_duration':True,'duration_seconds_range':{'minimum':1,'maximum':2147483647,'resource_limited':True}")
  compile(code,'facade.py','exec')
  # No restart of existing gateway, no changes to customer-service services.
  f.put(str(root/'backend/flex_video/proxy.py'),'/opt/harmonika-ai/video-routing/flex_video_proxy.py')
  backup='/opt/harmonika-ai/video-routing/facade.before-flex-20261101.py'
  try:
   assert f.stat(backup).st_size==0,'backup_already_exists'
   f.remove(backup)
  except FileNotFoundError:pass
  with f.open(backup,'wx') as x:x.write(old)
  temp='/opt/harmonika-ai/video-routing/facade.flex-next.py'
  with f.open(temp,'w') as x:x.write(code)
  latest=f.open('/opt/harmonika-ai/video-routing/facade.py').read()
  assert hashlib.sha256(latest).hexdigest()==hashlib.sha256(old.encode()).hexdigest(),'live_facade_changed'
  f.posix_rename(temp,'/opt/harmonika-ai/video-routing/facade.py')
  (root/'work/video-duration-20261101/facade.activated.py').write_text(code)
 print(json.dumps({'activated':True,'existing_services_restarted':False,'backup':backup}))

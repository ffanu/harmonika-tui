import sys
sys.path.insert(0,'/Users/id/Projects/hai-studio-20261101');from remote import connect
code='''import base64,io,json,secrets,urllib.request
from pathlib import Path
from PIL import Image,ImageDraw
owner='a'*64
reqid=secrets.token_hex(16)
im=Image.new('RGB',(512,288),'#f0f3f8');d=ImageDraw.Draw(im);d.rounded_rectangle((130,100,380,225),radius=18,fill='#2375b8');d.line((170,100,160,30),fill='#133b61',width=8);d.line((340,100,350,30),fill='#133b61',width=8)
b=io.BytesIO();im.save(b,format='PNG')
body={'action':'submit','owner':owner,'request_id':reqid,'input':{'kind':'video.animate_image','prompt':'A blue wireless internet router on a clean light background. Slow subtle cinematic camera push in. Preserve the router shape and colors. No text.','duration_seconds':10,'image_base64':base64.b64encode(b.getvalue()).decode()}}
request=urllib.request.Request('http://127.0.0.1:18258/rpc',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+Path('/etc/harmonika-ai/video-toolkit.token').read_text().strip()})
with urllib.request.urlopen(request,timeout=15) as r:out=json.load(r)
Path('/var/lib/harmonika-flex-video/synthetic-qualification.json').write_text(json.dumps({'owner':owner,'request_id':reqid,'job_id':out.get('job_id')}))
print(json.dumps(out))
'''
with connect() as c:
 i,o,e=c.exec_command('python3 -',timeout=25);i.write(code);i.channel.shutdown_write();print(o.read().decode());print(e.read().decode());assert o.channel.recv_exit_status()==0

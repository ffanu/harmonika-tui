"""Real, synthetic-image qualification. Never reads customer files or account keys."""
import json,re,time,urllib.request
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).parent;MODEL='qwen3-vl:2b-instruct';BASE='http://127.0.0.1:11439';op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def call(path,body=None):
    request=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with op.open(request,timeout=125) as r:return json.load(r)
def fixture(path,invoice,total,status,button):
    img=Image.new('RGB',(768,512),'#eef2f6');d=ImageDraw.Draw(img)
    font=lambda size:ImageFont.truetype('C:/Windows/Fonts/arial.ttf',size)
    d.rectangle((0,0,768,72),fill='#124b82');d.text((32,18),'HARMONIKA QA',fill='white',font=font(30))
    d.rounded_rectangle((26,100,742,490),radius=16,fill='white');d.text((50,118),'Invoice overview',fill='#102030',font=font(26))
    for y,label,value in [(182,'Invoice',invoice),(246,'Total',total),(310,'Status',status)]:
        d.text((52,y),label,fill='#607080',font=font(25));d.text((290,y),value,fill='#152535',font=font(30))
    d.rounded_rectangle((52,398,550,464),radius=10,fill='#1269d3');d.text((74,412),button,fill='white',font=font(28));img.save(path)
import base64
cases=[('HMS4827','Rp170.940','PAID','Download Receipt'),('INV7391','Rp245.000','PENDING','Check Payment')];results=[];start=time.monotonic();gpu=None
try:
    for i,(invoice,total,status,button) in enumerate(cases):
        path=ROOT/('qa-vision-'+str(i)+'.png');fixture(path,invoice,total,status,button)
        prompt='Read the screenshot. Return JSON only with keys invoice, total, status, primary_button. Copy the visible values exactly. Do not invent missing values.'
        t=time.monotonic();out=call('/api/chat',{'model':MODEL,'messages':[{'role':'user','content':prompt,'images':[base64.b64encode(path.read_bytes()).decode()]}],'stream':False,'think':False,'keep_alive':30,'options':{'temperature':0,'num_ctx':4096,'num_predict':256,'num_gpu':99}})
        text=out.get('message',{}).get('content','');data=json.loads(re.search(r'\{.*\}',text,re.S).group())
        assert str(data['invoice']).replace(' ','')==invoice,'invoice_read_failed'
        assert re.sub(r'\D','',str(data['total']))==re.sub(r'\D','',total),'total_read_failed'
        assert str(data['status']).upper()==status,'status_read_failed'
        assert str(data['primary_button']).casefold()==button.casefold(),'button_read_failed'
        ps=call('/api/ps');models=ps.get('models',[]);m=next((x for x in models if x.get('name')==MODEL or x.get('model')==MODEL),None)
        assert m and m.get('size_vram',0)>0,'gpu_execution_not_confirmed';gpu={k:m.get(k) for k in ('size','size_vram','context_length')}
        results.append({'fixture':i,'invoice':data['invoice'],'total':data['total'],'status':data['status'],'primary_button':data['primary_button'],'seconds':round(time.monotonic()-t,2)})
    result={'status':'passed','cases':results,'gpu':gpu,'elapsed_seconds':round(time.monotonic()-start,2),'inference_endpoint':'loopback','external_inference_used':False};(ROOT/'local-vision-smoke.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
finally:
    try:call('/api/generate',{'model':MODEL,'keep_alive':0})
    except Exception:pass

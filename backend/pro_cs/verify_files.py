"""Synthetic parser fixtures; no customer artifacts are used."""
import io,json,base64,zipfile
from readers import read
def check(name,raw,needle='HARMONIKA'):
 r=read({'name':name,'data_base64':base64.b64encode(raw).decode()});assert needle in r['text'],name;return {'format':name.split('.')[-1],'status':r['status'],'visual_model_invoked':False}
def main():
 out=[]
 from reportlab.pdfgen.canvas import Canvas
 b=io.BytesIO();p=Canvas(b);p.drawString(30,700,'HARMONIKA 123');p.save();out.append(check('fixture.pdf',b.getvalue()))
 from docx import Document
 b=io.BytesIO();p=Document();p.add_paragraph('HARMONIKA 123');p.save(b);out.append(check('fixture.docx',b.getvalue()))
 from openpyxl import Workbook
 b=io.BytesIO();p=Workbook();p.active['A1']='HARMONIKA 123';p.save(b);out.append(check('fixture.xlsx',b.getvalue()))
 from pptx import Presentation
 from pptx.util import Inches
 b=io.BytesIO();p=Presentation();slide=p.slides.add_slide(p.slide_layouts[6]);slide.shapes.add_textbox(Inches(1),Inches(1),Inches(5),Inches(1)).text='HARMONIKA 123';p.save(b);out.append(check('fixture.pptx',b.getvalue()))
 from PIL import Image,ImageDraw,ImageFont
 image=Image.new('RGB',(850,150),'white');ImageDraw.Draw(image).text((20,20),'HARMONIKA 123',font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',60),fill='black')
 for ext,fmt in [('png','PNG'),('jpg','JPEG'),('tiff','TIFF')]:
  b=io.BytesIO();image.save(b,format=fmt);out.append(check('fixture.'+ext,b.getvalue()))
 for name,raw in [('fixture.txt',b'HARMONIKA 123'),('fixture.json',b'{"name":"HARMONIKA 123"}'),('fixture.csv',b'name,value\nHARMONIKA,123'),('fixture.xml',b'<name>HARMONIKA</name>'),('fixture.svg',b'<svg xmlns="http://www.w3.org/2000/svg"><text>HARMONIKA</text></svg>'),('fixture.yaml',b'name: HARMONIKA'),('fixture.html',b'<p>HARMONIKA</p>'),('fixture.md',b'# HARMONIKA'),('fixture.ics',b'BEGIN:VCALENDAR\nSUMMARY:HARMONIKA\nEND:VCALENDAR'),('fixture.vcf',b'BEGIN:VCARD\nFN:HARMONIKA\nEND:VCARD'),('fixture.eml',b'Subject: HARMONIKA\nContent-Type: text/plain\n\nHARMONIKA')]:out.append(check(name,raw))
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w') as z:z.writestr('fixture.txt','HARMONIKA')
 out.append(check('fixture.zip',b.getvalue()))
 print(json.dumps({'ok':True,'real_fixture_count':len(out),'formats':out}))
if __name__=='__main__':main()

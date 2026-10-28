"""Bounded redacted findings and real portable PDF; no compliance certification."""
from .common import fields,need,text,integer,safe_id,redact,digest
REPORT_OPS=frozenset(('audit.report.generate','audit.report.executive','audit.report.technical','cicd.block.if.critical'))
def findings_input(v):
 fields(v,['findings','format','coverage_complete'],['findings']);rows=v['findings'];need(type(rows) is list and len(rows)<=200,'invalid_findings');out=[];seen=set()
 for row in rows:
  fields(row,['id','rule_id','severity','line','sha256'],['id','rule_id','severity','sha256']);key=safe_id(row['id']);need(key not in seen,'duplicate_finding');seen.add(key);rule=safe_id(row['rule_id']);severity=row['severity'];need(type(severity) is str and severity in ('critical','high','medium','low','info'),'invalid_severity');sha=text(row['sha256'],64);need(__import__('re').fullmatch('[0-9a-f]{64}',sha),'invalid_evidence_hash');out.append({'id':key,'rule_id':rule,'severity':severity,'sha256':sha,**({'line':integer(row['line'],1)} if 'line' in row else {})})
 need(type(v.get('coverage_complete',False)) is bool,'invalid_coverage');return out

def pdf_document(lines):
 import textwrap
 pages=[];flat=[]
 for line in lines:
  ascii_line=line.encode('ascii','replace').decode();flat.extend(textwrap.wrap(ascii_line,width=94) or [''])
 for n in range(0,len(flat),48):pages.append(flat[n:n+48])
 need(len(pages)<=20,'report_page_limit');objects=[b'',b''];font_id=3;objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');page_ids=[]
 for rows in pages:
  commands=['BT /F1 10 Tf 45 790 Td 15 TL']
  for line in rows:
   escaped=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)');commands.append('('+escaped+') Tj T*')
  commands.append('ET');stream='\n'.join(commands).encode('ascii');page_id=len(objects)+1;content_id=page_id+1;page_ids.append(page_id)
  objects.append(('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents '+str(content_id)+' 0 R >>').encode());objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
 objects[0]=b'<< /Type /Catalog /Pages 2 0 R >>';objects[1]=('<< /Type /Pages /Count '+str(len(page_ids))+' /Kids ['+' '.join(str(i)+' 0 R' for i in page_ids)+'] >>').encode();raw=b'%PDF-1.4\n';offsets=[0]
 for index,obj in enumerate(objects,1):offsets.append(len(raw));raw+=str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
 start=len(raw);raw+=('xref\n0 '+str(len(objects)+1)+'\n0000000000 65535 f \n').encode()+b''.join(('%010d 00000 n \n'%n).encode() for n in offsets[1:]);raw+=('trailer\n<< /Size '+str(len(objects)+1)+' /Root 1 0 R >>\nstartxref\n'+str(start)+'\n%%EOF\n').encode();return raw

def report(op,v):
 import base64,collections
 rows=findings_input(v);counts=dict(collections.Counter(r['severity'] for r in rows));complete=v.get('coverage_complete',False)
 if op=='cicd.block.if.critical':
  need('format' not in v,'irrelevant_format');return {'status':'decision_from_supplied_findings','block':not complete or counts.get('critical',0)>0,'coverage_assertion_verified':False,'pipeline_modified':False,'counts':counts,'external_notification_sent':False}
 fmt=v.get('format','markdown');need(fmt in ('markdown','pdf'),'invalid_report_format');lines=['HARMONIKA AI - DEBUG / SECURITY REPORT','Scope: supplied evidence only; no complete security or compliance certification.','Root cause, exploitation and remediation are not independently verified.','Findings: '+str(len(rows)),'Counts: '+', '.join(k+'='+str(counts.get(k,0)) for k in ['critical','high','medium','low','info']),'']
 if op!='audit.report.executive':
  for row in rows:lines.extend([row['id']+' | '+row['severity'].upper()+' | '+row['rule_id'],'Evidence SHA256: '+row['sha256']+(' | line '+str(row['line']) if 'line' in row else ''),''])
 result={'status':'report_generated','counts':counts,'findings':rows if op!='audit.report.executive' else [],'certified':False,'remediation_performed':False,'raw_evidence_embedded':False,'report_text':'\n'.join(lines)}
 if fmt=='pdf':
  raw=pdf_document(lines);result['artifact']={'name':'harmonika-debug-report.pdf','mime_type':'application/pdf','sha256':digest(raw),'base64':base64.b64encode(raw).decode()}
 return result

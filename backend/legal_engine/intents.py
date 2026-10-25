"""Conservative Indonesian triage and honest educational lenses, not lawyer credentials."""
import re
from .common import require
S={'type':'string','minLength':1,'maxLength':100};TEXT={'type':'string','minLength':1,'maxLength':8000};HANDLERS={};SCHEMAS={};MUTATIONS=set();LIMITATIONS={}
DISCLAIMER='Edukasi hukum, bukan nasihat hukum formal atau pengganti advokat.'
def obj(p,r=None):return {'type':'object','properties':p,'required':list(p) if r is None else r,'additionalProperties':False}
def reg(id,schema,fn,write=False,note='Conservative local educational triage, not a legal determination.'):
 HANDLERS[id]=fn;SCHEMAS[id]=schema;LIMITATIONS[id]=note
 if write:MUTATIONS.add(id)
def normalized(t):return re.sub(r'\s+',' ',t.lower()).strip()
def legal(c,a):
 t=normalized(a['text']);report=bool(re.search(r'(?<!bukan )\b(mau lapor|ingin melapor|saya korban|saya pelapor)\b',t));accused=bool(re.search(r'\b(saya dituduh|saya dilaporkan|saya terlapor|aku dituduh)\b',t));perspective='pelapor' if report and not accused else 'terlapor' if accused and not report else 'netral'
 return {'perspective':perspective,'needs_clarification':report==accused,'method':'bounded_phrase_triage','legal_conclusion':None,'disclaimer':DISCLAIMER}
reg('intent.detect.legal',obj({'text':TEXT}),legal)
def emergency(c,a):
 t=normalized(a['text']);signals=[];negated=[]
 for phrase in ('sedang dipukul','mau bunuh diri','diancam sekarang','dikurung','sedang diserang','pendarahan'):
  for match in re.finditer(re.escape(phrase),t):
   prefix=t[max(0,match.start()-40):match.start()]
   if re.search(r'(?:tidak|bukan|nggak|gak|ga|tak|enggak)(?: lagi)?\s*$',prefix):negated.append(phrase)
   else:signals.append(phrase)
 detected=bool(signals);uncertain=bool(negated) or bool(re.search(r'\b(contoh|hipotetis|cerita fiksi)\b',t))
 return {'urgent_safety_signal':detected,'needs_clarification':uncertain,'confirmed_emergency':False,'suggestion':'Utamakan tempat aman dan bantuan darurat setempat bila bahaya sedang berlangsung.' if detected else None,'legal_assessment_performed':False}

reg('intent.detect.emergency',obj({'text':TEXT}),emergency)
def service(c,a):
 t=normalized(a['text']);options=[]
 for key,words in [('lapor_polisi',('lapor polisi','buat laporan')),('advokasi',('pengacara','advokat','pendamping','lbh')),('edukasi',('jelaskan','apa itu','belajar','pasal'))]:
  if any(w in t for w in words):options.append(key)
 return {'requested_services':options,'needs_clarification':len(options)!=1,'automatic_report_or_contact':False}
reg('intent.detect.layanan',obj({'text':TEXT}),service)
LENSES={'ite':'Informasi dan transaksi elektronik','pidana':'Hukum pidana','perdata':'Hukum perdata','keluarga':'Hukum keluarga','tenagakerja':'Ketenagakerjaan','konsumen':'Perlindungan konsumen','pdp':'Pelindungan data pribadi'}
def detect(c,a):
 t=normalized(a['text']);matches=[k for k in LENSES if re.search(r'\b'+k+r'\b',t)];return {'requested_lenses':matches,'needs_clarification':len(matches)!=1,'licensed_expert_present':False}
reg('persona.detect.expert',obj({'text':TEXT}),detect)
def activate(c,a,lens):
 case=c.get('legal_case',a['case_id']);c.update('legal_case',case['id'],{'educational_lens':lens});c.event('legal.lens.changed',{'case_id':case['id']});return {'case_id':case['id'],'educational_lens':lens,'licensed_expert_present':False,'expert_impersonation':False,'disclaimer':DISCLAIMER}
for lens in LENSES:reg('persona.activate.'+lens,obj({'case_id':S}),lambda c,a,l=lens:activate(c,a,l),True,'Educational analytical lens only; no licensed professional identity or formal opinion is claimed.')
reg('persona.deactivate',obj({'case_id':S}),lambda c,a:activate(c,a,None),True)

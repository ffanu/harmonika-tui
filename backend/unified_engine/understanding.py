"""Conservative routing rules; not a calibrated NLU/emotion model or fact checker."""
import json,re
from .linguistics import _NORMALIZE_MAP,_PRIVATE_RE,_CRISIS_RE,_DESTRUCTIVE_RE,_HIGH_RE,_MEDIUM_RE,_PII,_TRIGGER_PATTERNS
WORDS={'yg':'yang','dgn':'dengan','tdk':'tidak','gk':'tidak','gak':'tidak','ga':'tidak','nggak':'tidak','bkn':'bukan','bkin':'bikin','bikinin':'buatkan','bknya':'bukannya','sy':'saya','sya':'saya','aku':'saya','mauu':'mau','bs':'bisa','bsa':'bisa','apk':'aplikasi','dr':'dari','utk':'untuk','gmn':'bagaimana','gmna':'bagaimana','knp':'kenapa','sm':'sama','udh':'sudah','sdh':'sudah','blm':'belum','makasih':'terima kasih','thx':'terima kasih'}
WORDS.update(_NORMALIZE_MAP)
PROTECTED=re.compile(r'```[\s\S]*?(?:```|\Z)|`[^`\n]*`|https?://[^\s<>]+|\b\w*\d\w*(?:[.,:/+-]\w+)*\b',re.I)
QUOTED=re.compile(r'```[\s\S]*?(?:```|\Z)|`[^`\n]*`|https?://[^\s<>]+|"[^"\n]*"|“[^”\n]*”|«[^»\n]*»')
ROUTES={'web_builder':r'\b(website|situs|web|fullstack|toko online|landing page)\b','mobile_builder':r'\b(android|ios|aplikasi mobile|aplikasi|flutter|react native|play store|app store)\b','legal':r'\b(hukum|pasal|uu ite|kuhp|pidana|perdata|pengacara|dituduh|dilaporkan)\b','browser_auto':r'\b(scraping|scrape|otomasi browser|automasi browser|imacros|klik otomatis)\b','text_typo':r'\b(perbaiki typo|koreksi typo|normalisasi teks|koreksi ejaan)\b'}
ROUTES['legal']='|'.join(_TRIGGER_PATTERNS['legal'])
NEG=re.compile(r'\b(jangan|bukan|tidak|tanpa|hindari)\b',re.I)
BUILD=re.compile(r'\b(buat|buatkan|bikin|bangun|build|bantu|desain|scaffold|kembangkan|tambah|tambahkan)\b',re.I)
def check(ok,code):
 if not ok:raise ValueError(code)
def normalize(text):
 check(type(text) is str and len(text)<=16000,'invalid_text');changes=[];parts=[];pos=0
 def replace(m):
  word=m[0];new=WORDS.get(word.lower())
  if new is None:return word
  if len(changes)<100:changes.append({'from':word,'to':new})
  return new
 for match in PROTECTED.finditer(text):
  parts.append(re.sub(r'\b[^\W\d_]+\b',replace,text[pos:match.start()],flags=re.UNICODE));parts.append(match[0]);pos=match.end()
 parts.append(re.sub(r'\b[^\W\d_]+\b',replace,text[pos:],flags=re.UNICODE));out=''.join(parts)
 return {'text':out,'changes':changes,'protected_tokens_preserved':PROTECTED.findall(text)==PROTECTED.findall(out),'mode':'bounded_rules'}
def analyze(messages):
 check(type(messages) is list and 1<=len(messages)<=40,'invalid_messages');total=0
 for m in messages:
  check(type(m) is dict and set(m)=={'role','content'} and m['role'] in ('system','user','assistant') and type(m['content']) is str and 0<len(m['content'])<=16000,'invalid_message');total+=len(m['content'])
 check(total<=48000 and messages[-1]['role']=='user','invalid_context')
 n=normalize(messages[-1]['content']);text=QUOTED.sub(' ',n['text']).lower();intents=[];negated=[];evidence=[]
 for route,pattern in ROUTES.items():
  for match in re.finditer(pattern,text):
   start=max(text.rfind(x,0,match.start()) for x in ('.',';','!','?','\n'))+1;prefix=text[start:match.start()]
   # A positive clause after tetapi/tapi/namun gets its own negation scope.
   prefix=re.split(r'\b(?:tapi|tetapi|namun|melainkan)\b',prefix)[-1]
   if ',' in prefix and BUILD.search(prefix.rsplit(',',1)[-1]):prefix=prefix.rsplit(',',1)[-1]
   if NEG.search(prefix[-70:]):negated.append(route);continue
   if route in ('web_builder','mobile_builder') and not BUILD.search(prefix):continue
   if route not in intents:intents.append(route);evidence.append({'rule':route+'_lexical','source':'latest_user'})
 # Resolve only a short answer to the immediately preceding explicit question.
 if not intents and len(text.strip())<=60 and len(messages)>1 and messages[-2]['role']=='assistant':
  prior=messages[-2]['content'].lower()
  if '?' in prior and re.search(r'\b(platform|android atau ios|mobile atau web|website atau aplikasi)\b',prior):
   if re.search(r'\b(android|ios|mobile|aplikasi)\b',text) and not NEG.search(text):intents=['mobile_builder'];evidence.append({'rule':'explicit_platform_followup','source':'previous_assistant_question'})
 urgency='low'
 for level,pattern in [('medium',_MEDIUM_RE),('high',_HIGH_RE)]:
  if any(not NEG.search(text[max(0,m.start()-40):m.start()]) for m in pattern.finditer(text)):urgency=level
 for m in re.finditer(_CRISIS_RE.pattern+r'|\b(sedang diserang|sedang dipukul|diancam sekarang|sesak napas parah)\b',text):
  prefix=text[max(0,m.start()-70):m.start()]
  if not NEG.search(prefix) and not re.search(r'\b(contoh|fiksi|kutipan|berita|cerita tentang)\b',prefix):urgency='crisis'
 privacy='confidential' if re.search(r'\b(password|kata sandi|otp|api key|token rahasia)\b',text) else 'private' if _PRIVATE_RE.search(text) else 'unspecified'
 for label,pattern in _PII:
  count=len(pattern.findall(messages[-1]['content']))
  if count:privacy='confidential';evidence.append({'rule':'pii','label':label,'count':count})
 unclear=bool(re.fullmatch(r'\s*(itu|yang tadi|lanjutkan itu|yang itu|buat itu|ubah itu)\s*[.!?]*\s*',text)) and not intents
 destructive=any(not NEG.search(text[max(0,m.start()-60):m.start()]) for m in _DESTRUCTIVE_RE.finditer(text))
 if destructive:unclear=True
 if not intents:intents=['chat']
 return {'normalized_text':n['text'],'intents':intents,'negated_intents':list(dict.fromkeys(negated)),'domains':intents,'urgency':urgency,'privacy':privacy,'ambiguity':unclear,'needs_clarification':unclear,'clarification':('Mohon konfirmasi lingkup data yang dimaksud, Kak. Tindakan destruktif belum dijalankan.' if destructive else 'Boleh jelaskan bagian yang ingin dibuat atau diubah, Kak?') if unclear else None,'mode':'bounded_rules','confidence':None,'evidence':evidence,'history_used':'only_immediate_explicit_question_when_needed','facts_verified':False}
def aggregate(results):
 check(type(results) is list and len(results)<=8,'invalid_results');check(len(json.dumps(results,allow_nan=False).encode())<=100000,'result_limit');sources=[];claims={};conflicts=[]
 def public(value):
  if type(value) is dict:return {k:public(v) for k,v in value.items() if k not in ('reasoning','reasoning_content','thinking','chain_of_thought','internal_pii_value')}
  if type(value) is list:return [public(v) for v in value]
  return value
 for i,r in enumerate(results):
  r=public(r)
  check(type(r) is dict and type(r.get('engine')) is str and 0<len(r['engine'])<=60,'invalid_result');sources.append({'engine':r['engine'],'source_index':i,'result':r,'independently_verified':False})
  data=r.get('data',{})
  if type(data) is dict:
   for k,v in data.items():
    if type(v) in (str,int,float,bool) or v is None:
     previous=claims.setdefault(k,[])
     if previous and any(x['value']!=v for x in previous):conflicts.append({'field':k,'sources':[x['engine'] for x in previous]+[r['engine']],'resolution':'requires_review'})
     previous.append({'engine':r['engine'],'value':v})
 return {'mode':'provenance_preserving_aggregation','results':sources,'conflicts':conflicts,'status':'needs_review' if conflicts else 'aggregated','facts_independently_verified':False,'reasoning_tokens_exposed':False}

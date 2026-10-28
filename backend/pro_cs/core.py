"""Bounded, source-aware draft planning. No network, side effects or calibrated confidence claims."""
import re,json,time,hashlib
from datetime import datetime,timezone
class Error(ValueError):pass
MAX_TEXT=32000
SAFE=re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')
def text(x,limit=MAX_TEXT):
 if not isinstance(x,str) or len(x.encode())>limit:raise Error('invalid_text')
 return x

def normalized(s):
 s=text(s).casefold();s=re.sub(r'\b(gk|gak|ga|nggak|tdk)\b','tidak',s);s=re.sub(r'\b(blm|blom|belom)\b','belum',s);s=re.sub(r'\b(mksd|maksudny)\b','maksud',s);return s

def detect(s):
 n=normalized(s);neg=bool(re.search(r'\b(?:belum|tidak|jangan)\b',n));intents=[]
 for name,pattern in [('billing',r'bayar|tagih|invoice|transfer|qris|lunas|bukti'),('coverage',r'cakup|cover|lokasi|maps|koordinat'),('packages',r'paket|harga|mbps|berlangganan|internet harmonika'),('outage',r'gangguan|lemot|putus|los\b|mati|buffer|belum normal'),('human',r'cs manusia|cs harmonika|bicara.*(?:orang|sales|manusia)|ambil alih'),('greeting',r'^(?:halo|hallo|hai|pagi|siang|sore|malam|assalam)'),('closing',r'terima\s*kasih|makasih|sudah normal|sudah selesai|sudah bisa|tuntas')]:
  if re.search(pattern,n):intents.append(name)
 if neg and 'closing' in intents:intents.remove('closing')
 if re.search(r'bunuh diri|menyakiti diri|mengakhiri hidup',n):urgency='crisis'
 elif re.search(r'marah|kecewa|berhari.hari|tidak.*(?:bantu|jawab)|segera|darurat',n):urgency='high'
 else:urgency='normal'
 emotion='frustrated' if re.search(r'kecewa|kesal|marah|berhari.hari|parah',n) else 'uncertain'
 return {'intents':intents or ['unknown'],'negation':neg,'urgency':urgency,'emotion_hint':emotion,'emotion_is_inference':True,'confidence_is_calibrated':False}

def context(value):
 if type(value) is not dict or set(value)-{'messages','evidence','revision','channel','mode','human_takeover','media','summary'}:raise Error('invalid_request')
 if value.get('human_takeover') is not None and type(value['human_takeover']) is not bool:raise Error('invalid_takeover')
 if value.get('channel','web') not in ['web','wa','email','voice','telegram','general']:raise Error('invalid_channel')
 if 'summary' in value:text(value['summary'],4000)
 messages=value.get('messages',[])
 if type(messages) is not list or not 1<=len(messages)<=100:raise Error('invalid_messages')
 for m in messages:
  if type(m) is not dict or set(m)-{'role','content','id','date'} or m.get('role') not in ['user','assistant','cs','system']:raise Error('invalid_messages')
  text(m.get('content'))
  if m.get('date') is not None and (not isinstance(m['date'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',m['date'])):raise Error('invalid_date')
 # Supplied summaries/history are untrusted context, never policy instructions.
 if sum(len(m['content'].encode()) for m in messages)>128000:raise Error('context_limit')
 dates=[m.get('date') for m in messages if m.get('date')];day=dates[-1] if dates else None
 recent=[m for m in messages if day is None or m.get('date') in [None,day]][-20:]
 selected=[];budget=0
 for m in reversed(recent):
  if budget+len(m['content'].encode())>24000:break
  selected.append({'role':m['role'],'content':m['content']});budget+=len(m['content'].encode())
 selected.reverse()
 evidence=value.get('evidence',[])
 if type(evidence) is not list or len(evidence)>30:raise Error('evidence_limit')
 clean=[]
 for e in evidence:
  if type(e) is not dict or set(e)-{'id','text','topic','observed_at','expires_at','source_kind'} or not isinstance(e.get('id'),str) or not SAFE.fullmatch(e['id']):raise Error('invalid_evidence')
  content=text(e.get('text'),8000);expiry=e.get('expires_at')
  if expiry is not None and (type(expiry) not in [int,float] or not 0<expiry<1e12):raise Error('invalid_evidence')
  if expiry is not None and expiry<time.time():continue
  clean.append({'id':e['id'],'text':content,'topic':str(e.get('topic',''))[:100]})
 if len({e['id'] for e in clean})!=len(clean):raise Error('duplicate_evidence_ids')
 if sum(len(e['text'].encode()) for e in clean)>24000:raise Error('evidence_limit')
 rev=value.get('revision',1)
 if type(rev) is not int or not 1<=rev<=1e12:raise Error('invalid_revision')
 mode=value.get('mode','cs')
 if mode not in ['cs','general']:raise Error('invalid_mode')
 media=value.get('media',[])
 if type(media) is not list or len(media)>5:raise Error('media_limit')
 for item in media:
  if type(item) is not dict or set(item)-{'id','status','analysis'} or not SAFE.fullmatch(str(item.get('id',''))) or item.get('status') not in ['pending','processed','failed']:raise Error('invalid_media')
  text(item.get('analysis',''),4000)
 return selected,clean,rev,mode,media

def plan(value):
 selected,evidence,rev,mode,media=context(value);last=selected[-1];raw=last['content'];signals=detect(raw)
 out={'schema_version':1,'status':'completed','mode':mode,'revision':rev,'auto_send':False,'delivery':'preview','needs_reply':True,'context':selected,'context_is_untrusted':True,'signals':signals,'source_assurance':'caller_provided_not_independently_verified','sources':[],'draft':None,'recommended_action':'clarify','confidence_is_calibrated':False}
 if value.get('human_takeover') is not None and type(value['human_takeover']) is not bool:raise Error('invalid_takeover')
 if value.get('human_takeover') or last['role']!='user':out.update(needs_reply=False,recommended_action='wait_for_customer');return out
 if any(m['status']!='processed' or not m.get('analysis','').strip() for m in media):out.update(status='degraded',recommended_action='await_media_analysis');return out
 intentions=signals['intents']
 if signals['urgency']=='crisis':out.update(recommended_action='urgent_human_review');return out
 if 'human' in intentions:out.update(recommended_action='human_handoff_requested',draft='Baik, Kak. Kami bantu teruskan ke '+('CS Harmonika' if mode=='cs' else 'tim dukungan')+' melalui percakapan ini.');return out
 if intentions==['closing'] or ('closing' in intentions and not any(i in intentions for i in ['billing','outage','packages','coverage'])):
  out.update(draft='Sama-sama, Kak. Terima kasih sudah menghubungi kami.',recommended_action='close_candidate');return out
 if intentions==['greeting']:
  out.update(draft='Halo, Kak! Ada yang bisa kami bantu?',recommended_action='preview');return out
 # Prefer context relevant to latest question; resolve short follow-ups against the last user question.
 topics=[i for i in intentions if i not in ['unknown','greeting','closing']]
 if not topics:
  for m in reversed(selected[:-1]):
   if m['role']=='user':
    topics=[i for i in detect(m['content'])['intents'] if i not in ['unknown','greeting','closing']]
    if topics:break
 words=set(re.findall(r'\w+',normalized(raw)));rank=[]
 for e in evidence:
  overlap=len(words & set(re.findall(r'\w+',normalized(e['text']))));match=e['topic'] in topics
  if match or overlap>=2:rank.append((10*match+overlap,e))
 rank.sort(key=lambda pair:pair[0],reverse=True);chosen=[e for _,e in rank[:3]]
 if chosen:
  # Deliberately extractive. Mentors may propose wording, which must pass separate validation.
  out.update(draft='Baik, Kak. Berdasarkan informasi yang tersedia:\n\n'+'\n\n'.join(e['text'] for e in chosen),sources=[e['id'] for e in chosen],recommended_action='review_grounded_draft')
 else:
  question='Boleh jelaskan informasi atau bagian yang ingin ditanyakan, Kak?'
  if 'packages' in topics:question='Untuk lokasi pemasangan mana, Kak? Kami perlu mencocokkan paket dan harga resmi yang berlaku.'
  elif 'coverage' in topics:question='Boleh kirim titik koordinat atau tautan Google Maps lokasi pemasangannya, Kak?'
  elif 'billing' in topics:question='Yang ingin Kakak tanyakan tagihan, cara pembayaran, atau pembayaran yang sudah dilakukan?'
  elif 'outage' in topics:question='Mohon maaf atas kendalanya, Kak. Boleh ceritakan kondisi terakhir dan lampu indikator modemnya agar CS Harmonika bisa menindaklanjuti dengan tepat?'
  out.update(draft=question.replace('CS Harmonika','tim dukungan') if mode=='general' else question,recommended_action='clarify_missing_evidence',knowledge_gap_topics=topics)
 return out

def redact(s):
 s=text(s);s=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email]',s)
 s=re.sub(r'(?<!\w)(?:\+?\d[\d -]{7,}\d)(?!\w)','[sensitive_number]',s)
 s=re.sub(r'(?i)\b(password|sandi|otp|pin|token|api.?key)\s*[:=]\s*\S+',r'\1: [redacted]',s)
 return s

def validate(value):
 if type(value) is not dict or set(value)-{'context','draft','draft_revision','revision','claims','source_ids','human_takeover'}:raise Error('invalid_validation')
 proposed=text(value.get('draft'));p=plan(value.get('context',{}));reasons=[]
 if type(value.get('revision')) is not int or value.get('draft_revision')!=value['revision'] or value['revision']!=p['revision']:reasons.append('stale_revision')
 if not p['needs_reply'] or value.get('human_takeover'):reasons.append('human_takeover_or_no_customer_message')
 if p['recommended_action'] in ['await_media_analysis','urgent_human_review']:reasons.append(p['recommended_action'])
 sources=value.get('source_ids',[])
 if type(sources) is not list or any(not isinstance(x,str) for x in sources):raise Error('invalid_sources')
 known={e['id']:e['text'] for e in context(value['context'])[1]}
 if any(x not in known for x in sources):reasons.append('unknown_source')
 # A reference attached to a hallucinated sentence is not evidence. Only verbatim
 # supported text (or the engine's deterministic safe template) is accepted.
 if proposed!=p['draft'] and proposed.strip() not in [known.get(x) for x in sources]:reasons.append('unsupported_claim_or_paraphrase_requires_review')
 if proposed!=redact(proposed):reasons.append('sensitive_data_requires_review')
 if re.search(r'(?i)sudah.*(?:refund|tiket|kompensasi|menambah.*kuota)|teknisi.*\d.*(?:jam|menit)|tidak ada gangguan|xendit.*(?:sukses|lunas)',proposed):reasons.append('external_action_or_live_claim_requires_connector')
 return {'schema_version':1,'status':'completed','approved_for_preview':not reasons,'auto_send':False,'reasons':sorted(set(reasons)),'revision':p['revision'],'confidence_is_calibrated':False}

def facts(value):
 s=text(value.get('text'));kind=value.get('kind');patterns={'amount':r'(?:Rp\.?|IDR)\s*([0-9][0-9.,]*)','date':r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)[a-z]*\s+\d{4})\b','contact':r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','location':r'(?<!\d)-?\d{1,2}\.\d+\s*,\s*-?\d{1,3}\.\d+'}
 if kind not in patterns:raise Error('unsupported_extractor')
 return {'status':'completed','matches':re.findall(patterns[kind],s,re.I)[:50],'interpretation':'literal_only_not_verified_transaction'}

"""Evidence-preserving mobile specification planning; does not train or execute an LLM."""
import copy,re
IDS=(
 'spec.read.mobile','spec.parse.platform','spec.parse.screens','spec.parse.features.mobile',
 'spec.parse.offline','spec.parse.auth.mobile','spec.parse.push_notif','spec.parse.iap',
 'spec.parse.design_pref.mobile','spec.clarify.mobile','stack.mobile.analyze',
 'stack.select.react_native','stack.select.flutter','stack.select.kotlin','stack.select.swift',
 'stack.select.ionic','stack.select.state_mgmt','stack.select.navigation','stack.select.network',
 'stack.select.local_db','stack.select.di','stack.recommend.combination.mobile',
 'arch.mobile.system','arch.mobile.screen_flow','arch.mobile.state','arch.mobile.offline',
 'arch.mobile.sync','arch.mobile.cache','arch.mobile.security','arch.mobile.local_storage',
 'arch.mobile.push_notif','arch.mobile.deep_link','arch.mobile.iap','arch.mobile.diagram')
FRAMEWORKS=('expo','react_native','flutter','kotlin','swift','ionic')
SCHEMA={'type':'object','required':['spec'],'additionalProperties':False,'properties':{
 'spec':{'type':'string','minLength':1,'maxLength':32000},
 'platform':{'type':'string','enum':['android','ios','both']},
 'framework':{'type':'string','enum':list(FRAMEWORKS)},
 'screens':{'type':'array','maxItems':30,'uniqueItems':True,'items':{'type':'string','pattern':'^[A-Z][A-Za-z0-9]{0,39}$'}},
 'features':{'type':'array','maxItems':40,'uniqueItems':True,'items':{'type':'string','maxLength':40}},
}}
FEATURES={'camera':r'kamera|camera','gallery':r'galeri|gallery|photo picker','gps':r'\bgps\b|lokasi|location',
 'push':r'push|notifikasi|notification','iap':r'in.app purchase|\biap\b|subscription|langganan digital',
 'offline':r'offline|luring','biometric':r'biometri|fingerprint|face id|touch id',
 'oauth':r'oauth|sign in with|login.*google|login.*apple','login':r'login|log in|autentikasi|authentication',
 'payment':r'payment|pembayaran|midtrans','bluetooth':r'bluetooth|\bble\b','maps':r'\bmaps\b|peta',
 'dark_mode':r'dark mode|mode gelap','cart':r'\bcart\b|keranjang','catalog':r'catalog|katalog'}
SCHEMA['properties']['features']['items']['enum']=list(FEATURES)
NEGATION=re.compile(r'\b(bukan|tanpa|jangan|tidak(?: perlu| butuh| pakai| ingin)?|no|not|without|exclude|disable)\b',re.I)
STACKS={
 'expo':{'ui':'React Native','state':'Zustand','navigation':'React Navigation','network':'fetch','local_db':'SQLite / AsyncStorage (non-sensitive)','secure_storage':'Expo SecureStore'},
 'react_native':{'ui':'React Native','state':'Zustand','navigation':'React Navigation','network':'fetch','local_db':'SQLite','secure_storage':'Keychain / Keystore bridge'},
 'flutter':{'ui':'Material 3 / Cupertino','state':'Riverpod','navigation':'GoRouter','network':'Dio','local_db':'Drift / SQLite','secure_storage':'flutter_secure_storage'},
 'kotlin':{'ui':'Jetpack Compose Material 3','state':'ViewModel + StateFlow','navigation':'Navigation Compose','network':'Retrofit + OkHttp','local_db':'Room','secure_storage':'Android Keystore'},
 'swift':{'ui':'SwiftUI','state':'ObservableObject / Observation','navigation':'NavigationStack','network':'URLSession + async/await','local_db':'Core Data / SwiftData after deployment-target validation','secure_storage':'Keychain'},
 'ionic':{'ui':'Ionic + Capacitor','state':'React state','navigation':'Ionic Router','network':'fetch','local_db':'SQLite plugin','secure_storage':'native Keychain / Keystore plugin'}}
def validate(v):
 if type(v) is not dict or set(v)-set(SCHEMA['properties']) or not isinstance(v.get('spec'),str) or not 1<=len(v['spec'])<=32000 or not v['spec'].strip():raise ValueError('invalid_input')
 for k,options in (('platform',('android','ios','both')),('framework',FRAMEWORKS)):
  if k in v and (type(v[k]) is not str or v[k] not in options):raise ValueError('invalid_'+k)
 for k,limit in (('screens',30),('features',40)):
  if k in v and (type(v[k]) is not list or len(v[k])>limit or any(type(x) is not str or not re.fullmatch(r'[A-Z][A-Za-z0-9]{0,39}' if k=='screens' else r'[a-z][a-z0-9_]{0,39}',x) for x in v[k]) or len(set(v[k]))!=len(v[k])):raise ValueError('invalid_'+k)
 if any(x not in FEATURES for x in v.get('features',[])):raise ValueError('unknown_feature')
 return v

def analyze(v):
 validate(v);text=v['spec'];clauses=re.split(r'[\n,;.!?]+|\b(?:tetapi|namun|but|dan gunakan|dan aktifkan|and use|and enable)\b',text,flags=re.I);evidence=[];found=set();excluded=set()
 for n,c in enumerate(clauses):
  for f,pattern in FEATURES.items():
   for m in re.finditer(pattern,c,re.I):
    before=c[max(0,m.start()-45):m.start()];neg=bool(NEGATION.search(before))
    (excluded if neg else found).add(f);evidence.append({'feature':f,'clause':n+1,'negated':neg})
 # An explicit structured feature cannot silently override textual exclusion.
 conflicts=sorted(excluded & (found|set(v.get('features',[]))))
 features=sorted((found|set(v.get('features',[])))-excluded)
 platforms=set();excluded_platforms=set()
 for c in clauses:
  for p,pat in (('android',r'\bandroid\b'),('ios',r'\bios\b|iphone|ipad')):
   for m in re.finditer(pat,c,re.I):
    (excluded_platforms if NEGATION.search(c[max(0,m.start()-35):m.start()]) else platforms).add(p)
 platform=v.get('platform') or ('both' if len(platforms)==2 else next(iter(platforms)) if platforms else None)
 choices=[];excluded_frameworks=set()
 for fw,pat in (('expo',r'\bexpo\b'),('react_native',r'react native'),('flutter',r'\bflutter\b'),('kotlin',r'\bkotlin\b'),('swift',r'\bswift\b'),('ionic',r'\bionic\b')):
  for c in clauses:
   m=re.search(pat,c,re.I)
   if m:
    if NEGATION.search(c[max(0,m.start()-35):m.start()]):excluded_frameworks.add(fw)
    elif fw not in choices:choices.append(fw)
 if 'expo' in choices and 'react_native' in choices:choices.remove('react_native')
 framework=v.get('framework') or (choices[0] if len(choices)==1 else None)
 questions=[]
 if (framework and framework in excluded_frameworks):questions.append('Framework terstruktur bertentangan dengan pengecualian dalam spec; perlu konfirmasi.')
 if (platform in excluded_platforms) or (platform=='both' and excluded_platforms):questions.append('Platform terstruktur bertentangan dengan pengecualian dalam spec; perlu konfirmasi.')
 if not platform:questions.append('Target aplikasi Android, iOS, atau keduanya?')
 if len(choices)>1 and 'framework' not in v:questions.append('Ada beberapa framework dalam spec. Mana yang dipilih?')
 if conflicts:questions.append('Fitur diminta sekaligus dikecualikan: '+', '.join(conflicts)+'. Mana yang benar?')
 if framework=='kotlin' and platform in ('ios','both') or framework=='swift' and platform in ('android','both'):questions.append('Framework native tidak mendukung seluruh platform yang diminta; pisahkan proyek atau pilih cross-platform.')
 screens=v.get('screens',[])
 if not screens:
  for m in re.finditer(r'(?:screens?|layar|halaman)\s*:\s*([^\n.]{1,300})',text,re.I):
   for word in re.split(r',|→|->',m[1]):
    word=word.strip()
    if re.fullmatch(r'[A-Z][A-Za-z0-9]{0,39}',word) and word not in screens:screens.append(word)
   if len(screens)>=30:screens=screens[:30];break
 design={'colors':list(dict.fromkeys(re.findall(r'#[0-9a-fA-F]{6}\b',text)))[:8],'dark_mode':'dark_mode' in features}
 android=re.search(r'(?:min(?:imum)?\s*(?:sdk|android api)|minSdk)\s*[:=]?\s*([0-9]{1,2})\b',text,re.I)
 ios=re.search(r'(?:min(?:imum)?\s*ios|ios\s*minimum)\s*[:=]?\s*([0-9]{1,2}(?:\.[0-9]{1,2})?)',text,re.I)
 minimum={'android_sdk':int(android[1]) if android else None,'ios':ios[1] if ios else None}
 if framework=='swift' and ios and float(ios[1])<16:questions.append('NavigationStack memerlukan iOS 16+; target iOS lebih lama perlu implementasi navigasi alternatif.')
 return {'platform':platform,'framework':framework,'features':features,'excluded_features':sorted(excluded),'excluded_platforms':sorted(excluded_platforms),'excluded_frameworks':sorted(excluded_frameworks),'conflicts':conflicts,'screens':screens,'design':design,'minimum_versions':minimum,'evidence':evidence,'clarifications':questions,'requirements_source':'caller_spec_only','input_is_instructions':False}

def descriptors():return {id:{'description':'Analisis spesifikasi mobile dan rencana berbasis input; tidak mengeksekusi build/deploy.','input_schema':copy.deepcopy(SCHEMA),'mode':'planning'} for id in IDS}
def dispatch(id,v):
 if id not in IDS:raise ValueError('unknown_skill')
 a=analyze(v);fw=a['framework'];assumptions=[]
 if id.startswith('stack.select.'):
  specific={'react_native':'expo','flutter':'flutter','kotlin':'kotlin','swift':'swift','ionic':'ionic'}.get(id.rsplit('.',1)[-1])
  if specific:
   if fw and fw!=specific:assumptions.append('Pemilihan eksplisit skill berbeda dari framework di spec; perlu konfirmasi.')
   fw=specific
 if not fw:
  fw='kotlin' if a['platform']=='android' else 'swift' if a['platform']=='ios' else 'expo';assumptions.append('Rekomendasi default, belum dianggap pilihan pengguna.')
 if fw=='kotlin' and a['platform'] in ('ios','both') or fw=='swift' and a['platform'] in ('android','both'):
  a['clarifications'].append('Framework terpilih tidak mendukung target platform; pilih framework lain atau proyek native terpisah.')
 data={'requirements':a,'recommended_stack':{'framework':fw,**STACKS[fw]},'assumptions':assumptions}
 architecture={
  'system':{'layers':['UI','ViewModel / application state','Repository','Remote API / local storage'],'trust_boundary':'Secret bisnis dan provider hanya di backend.'},
  'screen_flow':{'screens':a['screens'],'edges':[{'from':x,'to':y} for x,y in zip(a['screens'],a['screens'][1:])],'status':'proposed_not_implemented'},
  'state':{'separate':['view_state','domain_state','server_cache'],'states':['idle','loading','success','empty','error'],'cancel_stale_requests':True},
  'offline':{'enabled':'offline' in a['features'],'policy':'Cache hanya data yang diizinkan, tampilkan stale state, antre mutasi idempotent bila diminta.'},
  'sync':{'strategy':'outbox + idempotency key + revision check','conflicts':'Jangan overwrite perubahan server diam-diam; minta resolusi konflik.'},
  'cache':{'keys':'tenant + account + resource + query','logout':'hapus cache akun dan batalkan request aktif','ttl':'Ditentukan kebutuhan produk; belum ditetapkan.'},
  'security':{'controls':['HTTPS','backend authorization on every request','Keychain/Keystore for user token','no server secrets in APK/IPA','redacted logs'],'pinning':'Perlu rotasi sertifikat dan recovery terencana, bukan bypass TLS.'},
  'local_storage':{'sensitive':'Keychain / Keystore-backed storage','preferences':'non-sensitive key-value','structured':'SQLite schema with versioned migrations'},
  'push_notif':{'requested':'push' in a['features'],'flow':['ask permission in context','register device token with authenticated backend','deduplicate deliveries','validate deep link'],'server_credentials_in_app':False},
  'deep_link':{'flow':['allowlist schemes/hosts','validate path and parameters','resolve authentication','navigate after app ready'],'verification':'Asset Links / AASA harus dipasang di domain yang dikuasai.'},
  'iap':{'requested':'iap' in a['features'],'flow':['fetch store product','native purchase','server verify transaction','persist entitlement','handle restore/refund/webhook idempotently'],'ready_for_real_payment':False},
 }
 if id.startswith('arch.mobile.'):
  key=id.rsplit('.',1)[-1]
  data={'requirements':a,'architecture':architecture.get(key,architecture['system'])}
  if key=='diagram':data['mermaid']='flowchart TD\n  UI --> State\n  State --> Repository\n  Repository --> API\n  Repository --> LocalStorage\n  API --> BackendAuthorization'
 elif id.startswith('spec.parse.'):
  key=id.removeprefix('spec.parse.');field={'features.mobile':'features','auth.mobile':'authentication','push_notif':'push','design_pref.mobile':'design'}.get(key,key)
  values={**a,'authentication':[f for f in a['features'] if f in ('login','oauth','biometric')],**{k:k in a['features'] for k in ('offline','push','iap')}}
  data={'requirements':a,'parsed_field':field,'value':values.get(field),'clarifications':a['clarifications']}
 elif id=='spec.clarify.mobile':data={'clarifications':a['clarifications'],'conflicts':a['conflicts'],'requirements':a}
 return {'status':'completed','mode':'planning','data':data,'warnings':['Rencana berbasis spesifikasi; belum ada build, emulator, perangkat fisik, signing, atau publikasi store.'],'runtime_verified':False,'external_actions_performed':False}

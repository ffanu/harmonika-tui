"""Reconcile all numbered source IDs without treating registration as execution."""
import hashlib,json,re
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'backend/erp_engine';raw=(root/'specification.txt').read_bytes();text=raw.decode()
entries=[];layer=None
for line in text.splitlines():
 m=re.match(r'\[Layer (\d+)\] (.+?)(?: \(.*)?$',line)
 if m:layer={'number':int(m[1]),'name':m[2]}
 m=re.match(r'^\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.+)$',line)
 if not m:continue
 source=m[2].strip();canonical=re.sub(r'\s+','',source)
 entries.append({'number':int(m[1]),'id':'erp_all.'+canonical,'source_id':source,'operation':canonical,'description':m[3].strip(),'layer':layer})
assert [x['number'] for x in entries]==list(range(1,364));assert len({x['id'] for x in entries})==363
(root/'catalog.json').write_text(json.dumps({'schema_version':1,'source_sha256':hashlib.sha256(raw).hexdigest(),'source_lines':len(text.splitlines()),'declared_counts':[285,347],'actual_skills':363,'layers':28,'skills':entries},ensure_ascii=False,indent=2)+'\n')
# Profiles are spec-derived metadata, not claims of implemented specialty modules.
blocks=re.findall(r'(?ms)^(\d+)\. ([A-Z][^\n]+)\n(.*?)(?=^\d+\. [A-Z]|^={5,}|\Z)',text.split('INDUSTRY TEMPLATES (25+ INDUSTRI)',1)[1].split('CONTOH WORKFLOW',1)[0])
keys=['isp','retail','manufacturing','service','distribution','construction','property','mining','agriculture','education','healthcare','logistics','banking','insurance','automotive','food','pharmacy','agency','transportation','energy','mobile_telco','hospitality','events','consulting','saas']
assert len(blocks)==25
keywords=[['isp','rtrw net','rt rw net','internet provider','fiber provider','ftth','mbps'],['toko','retail','minimarket','kasir','butik'],['manufaktur','pabrik','manufacturing','bom','mrp'],['jasa','salon','repair','servis'],['distributor','grosir','wholesale'],['konstruksi','kontraktor','rab','mandor'],['properti','property','real estate','apartemen','sewa gedung'],['tambang','pertambangan','batubara','nikel'],['pertanian','perkebunan','panen','agro'],['sekolah','pendidikan','universitas','siswa','bimbel'],['rumah sakit','klinik','kesehatan','pasien','medical'],['logistik','ekspedisi','kurir','freight','shipment'],['bank','perbankan','fintech','p2p lending','koperasi'],['asuransi','underwriting','premi','polis'],['otomotif','bengkel','dealer','spare part'],['restoran','resto','cafe','kafe','catering','roti','f b','cloud kitchen'],['apotek','pharmacy','obat','resep'],['agensi','agency','advertising','digital marketing'],['transportasi','bus','travel','trucking','taxi'],['energi','plts','pltu','renewable','kwh'],['operator seluler','mobile operator','roaming','imsi'],['hotel','hospitality','villa','resort','penginapan'],['event organizer','wedding organizer','eo','konser'],['konsultan','consulting','kantor akuntan','konsultan pajak','konsultan hukum'],['saas','startup','software as a service','subscription software']]
modules=[['isp','crm','inventory','field_ops'],['pos','inventory','sales','crm','procurement'],['manufacturing','inventory','procurement','sales','projects'],['projects','crm','sales'],['inventory','procurement','sales','logistics'],['construction','projects','inventory','procurement'],['property','sales','crm'],['mining','inventory','maintenance'],['agriculture','inventory','procurement'],['education','scheduling'],['healthcare','pharmacy','inventory'],['logistics','inventory'],['banking','crm'],['insurance','crm'],['automotive','inventory','sales'],['food','pos','inventory','procurement'],['pharmacy','pos','inventory','procurement'],['projects','crm','campaign'],['transportation','logistics'],['energy','maintenance'],['mobile_telco','crm'],['hospitality','scheduling','food'],['events','projects','procurement'],['projects','crm','documents'],['saas','crm','support']]
colors=['#0066CC','#D9480F','#334155','#6D28D9','#047857','#854D0E','#854D0E','#92400E','#166534','#1D4ED8','#0F766E','#075985','#1E3A8A','#1D4ED8','#B91C1C','#C2410C','#15803D','#7E22CE','#1D4ED8','#4D7C0F','#0369A1','#92400E','#A21CAF','#334155','#4338CA']
profiles=[]
for i,((number,title,body),key) in enumerate(zip(blocks,keys)):
 theme=re.search(r'Theme:\s*(.+)',body);terms=re.search(r'terminology\s*"([^"]+)"',body)
 profiles.append({'id':key,'name':title,'source_number':int(number),'keywords':keywords[i],'required_modules':['core','finance',*modules[i]],'optional_modules':['hr','analytics','integration'],'primary_color':colors[i],'theme_direction':theme[1] if theme else '', 'terminology':terms[1].split('/') if terms else ['Data','Transaksi','Laporan'],'domain_runtime':'not_implemented','template_kind':'industry_plan'})
(root/'industries.json').write_text(json.dumps(profiles,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'skills':len(entries),'industry_profiles':len(profiles),'source_hash_verified':True}))

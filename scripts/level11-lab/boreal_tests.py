"""Held-out synthetic CSV contracts; no user data."""
import copy,csv,hashlib,json,os,stat,sys,tempfile,unittest,concurrent.futures,subprocess
from pathlib import Path
sys.path.insert(0,os.environ['HARMONIKA_EXAM_PROJECT'])
import report as m
HEADER='invoice_id,project,amount,status\n'
TEXT=HEADER+'a,one,0.10,paid\nb,one,0.20,paid\nc,one,0.05,refunded\nd,two,12.50,pending\ne,two,4.00,paid\n'
class Boreal(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
 def tearDown(self):self.tmp.cleanup()
 def test_01_decimal_exact(self):self.assertEqual(m.summary(m.parse_csv(TEXT))[0]['net_total'],'0.25')
 def test_02_project_same_invoice_independent(self):self.assertEqual(len(m.parse_csv(HEADER+'a,one,1,paid\na,two,2,paid\n')),2)
 def test_03_normalization_and_bom(self):self.assertEqual(m.parse_csv('\ufeff'+HEADER+' a , one , 1.2 , paid \n')[0],{'invoice_id':'a','project':'one','amount':'1.20','status':'paid'})
 def test_04_same_duplicate_dedup(self):self.assertEqual(len(m.parse_csv(HEADER+'a,one,1,paid\na,one,1.00,paid\n')),1)
 def test_05_conflicting_duplicate(self):
  with self.assertRaises(m.ReportError):m.parse_csv(HEADER+'a,one,1,paid\na,one,2,paid\n')
 def test_06_invalid_amount_grammar(self):
  for amount in ['NaN','Infinity','-1','+1','1e2','01.00','.5','1.','1.234','', '1000000000']:
   with self.subTest(amount=amount),self.assertRaises(m.ReportError):m.parse_csv(HEADER+'a,one,'+amount+',paid\n')
 def test_07_header_exact(self):
  for header in ['', 'project,invoice_id,amount,status\n','invoice_id,project,amount,amount\n','invoice_id,project,amount,status,extra\n']:
   with self.assertRaises(m.ReportError):m.parse_csv(header+'a,one,1,paid\n')
 def test_08_row_arity(self):
  for row in ['a,one,1\n','a,one,1,paid,x\n']:
   with self.assertRaises(m.ReportError):m.parse_csv(HEADER+row)
 def test_09_blank_lines_and_quoted_fields(self):self.assertEqual(len(m.parse_csv(HEADER+'\n"a","one","1.00","paid"\r\n\n')),1)
 def test_10_field_validation(self):
  for row in ['../a,one,1,paid\n','a,other,1,paid\n','a,one,1,unknown\n','a'*65+',one,1,paid\n']:
   with self.assertRaises(m.ReportError):m.parse_csv(HEADER+row)
 def test_11_input_bounds_types(self):
  for value in [None,[],b'bytes','x'*1048577]:
   with self.assertRaises(m.ReportError):m.parse_csv(value)
 def test_12_summary_sort_and_fields(self):
  rows=m.summary(m.parse_csv(TEXT));self.assertEqual([r['project'] for r in rows],['one','two']);self.assertEqual(rows[1],{'project':'two','paid_total':'4.00','refunded_total':'0.00','net_total':'4.00','pending_count':1,'invoice_count':2})
 def test_13_negative_net(self):self.assertEqual(m.summary(m.parse_csv(HEADER+'a,one,3,refunded\n'))[0]['net_total'],'-3.00')
 def test_14_snapshot_no_mutation(self):
  rows=m.parse_csv(TEXT);before=copy.deepcopy(rows);a=m.summary(rows);a[0]['paid_total']='999';self.assertEqual(rows,before);self.assertEqual(m.summary(rows)[0]['paid_total'],'0.30')
 def test_15_invalid_normalized_rows(self):
  for rows in [[{'project':'one'}],[{'invoice_id':'a','project':'one','amount':1.0,'status':'paid'}],m.parse_csv(TEXT)[:1]*2]:
   with self.assertRaises(m.ReportError):m.summary(rows)
 def test_16_exact_csv(self):
  raw=m.render_csv(m.summary(m.parse_csv(TEXT)));self.assertEqual(raw,b'project,paid_total,refunded_total,net_total,pending_count,invoice_count\r\none,0.30,0.05,0.25,0,3\r\ntwo,4.00,0.00,4.00,1,2\r\n')
 def test_17_empty(self):self.assertEqual(m.parse_csv(HEADER),[]);self.assertEqual(m.summary([]),[]);self.assertEqual(m.render_csv([]),b'project,paid_total,refunded_total,net_total,pending_count,invoice_count\r\n')
 def test_18_publish_bytes_manifest_mode(self):
  p=Path(m.publish(TEXT,self.root,'one'));self.assertEqual(p,self.root/'one/result.csv');raw=p.read_bytes();manifest=json.loads((p.parent/'manifest.json').read_text());self.assertEqual(manifest,{'schema':1,'request_id':'one','input_sha256':hashlib.sha256(TEXT.encode()).hexdigest(),'output_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)});self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600);self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode),0o700)
 def test_19_replay_preserves_bytes_mtime(self):
  p=Path(m.publish(TEXT,self.root,'one'));before=p.stat().st_mtime_ns;self.assertEqual(Path(m.publish(TEXT,self.root,'one')),p);self.assertEqual(p.stat().st_mtime_ns,before)
 def test_20_conflict_and_corrupt_artifact_refused(self):
  p=Path(m.publish(TEXT,self.root,'one'));original=p.read_bytes()
  with self.assertRaises(m.ReportError):m.publish(TEXT+'f,one,1,paid\n',self.root,'one')
  self.assertEqual(p.read_bytes(),original);p.write_bytes(b'foreign bytes')
  with self.assertRaises(m.ReportError):m.publish(TEXT,self.root,'one')
  self.assertEqual(p.read_bytes(),b'foreign bytes')
 def test_21_refuse_existing_incomplete_and_symlinks(self):
  (self.root/'existing').mkdir();(self.root/'existing/user.txt').write_text('preserve');(self.root/'link').symlink_to(self.root/'existing',target_is_directory=True)
  for key in ['existing','link','../outside','',True]:
   with self.assertRaises((m.ReportError,ValueError)):m.publish(TEXT,self.root,key)
  self.assertEqual((self.root/'existing/user.txt').read_text(),'preserve')
 def test_22_invalid_input_creates_nothing(self):
  with self.assertRaises(m.ReportError):m.publish(HEADER+'a,one,NaN,paid\n',self.root,'bad')
  self.assertEqual(list(self.root.iterdir()),[])
 def test_23_concurrent_publication(self):
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
   def call(_):
    try:return str(m.publish(TEXT,self.root,'same'))
    except (m.ReportError,FileExistsError):return None
   rows=list(pool.map(call,range(4)))
  self.assertTrue(any(rows));p=self.root/'same/result.csv';self.assertEqual(p.read_bytes(),m.render_csv(m.summary(m.parse_csv(TEXT))));self.assertEqual(Path(m.publish(TEXT,self.root,'same')),p)
 def test_24_cli_real_and_error_no_rawdata(self):
  code=self.root/'report.py';code.write_bytes((Path(os.environ['HARMONIKA_EXAM_PROJECT'])/'report.py').read_bytes());(self.root/'reports').mkdir();(self.root/'input.csv').write_text(TEXT);args=[sys.executable,str(code),'--input','input.csv','--output-root','reports','--request-id','cli'];p=subprocess.run(args,cwd=self.root,capture_output=True,text=True,timeout=10);self.assertEqual(p.returncode,0,p.stderr);v=json.loads(p.stdout);self.assertIs(v['completed'],True);out=self.root/v['file'];self.assertTrue(out.is_file());self.assertEqual(v['sha256'],hashlib.sha256(out.read_bytes()).hexdigest());(self.root/'input.csv').write_text(HEADER+'PRIVATE_DATA_CANARY,one,NaN,paid\n');p=subprocess.run(args,cwd=self.root,capture_output=True,text=True,timeout=10);self.assertEqual(p.returncode,2);self.assertNotIn('PRIVATE_DATA_CANARY',p.stdout+p.stderr);self.assertFalse(json.loads(p.stdout)['completed'])
if __name__=='__main__':unittest.main(verbosity=2)

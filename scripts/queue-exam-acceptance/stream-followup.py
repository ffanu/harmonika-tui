import unittest,sys,os,json
sys.path.insert(0,os.environ['HARMONIKA_EXAM_PROJECT']+'/src')
from streaming import SSEDecoder,JobFollower
def e(id,v,state='running',**extra):return dict(id=str(id),event='job',data=json.dumps(dict(job_id='j',version=v,state=state,**extra)))
def wire(event):return ('id: '+event['id']+'\nevent: job\ndata: '+event['data']+'\n\n').encode()
class Followup(unittest.TestCase):
 def test_numeric_cursor_ordering_and_leading_zeros(self):
  f=JobFollower('j');f.apply(e('09',1));self.assertTrue(f.apply(e('10',2)));self.assertFalse(f.apply(e('0009',3)));self.assertFalse(f.apply(e('0010',3)));self.assertEqual(f.summary()['cursor'],'10')
 def test_timeout_reconnect_uses_accepted_cursor_and_closes(self):
  f=JobFollower('j');calls=[];closed=[]
  def connect(cursor):
   calls.append(cursor)
   try:
    if len(calls)==1:yield wire(e(1,1));raise TimeoutError('fixture')
    yield wire(e(2,2,'completed',result_ref='file'))
   finally:closed.append(True)
  self.assertEqual(f.follow(connect)['state'],'completed');self.assertEqual(calls,[None,'1']);self.assertEqual(len(closed),2)
 def test_decode_and_apply_errors_reconnect_and_close(self):
  for bad in [b'data: \xff',b'data: \xe6',b'id: 9\nevent: job\ndata: bad\n\n']:
   f=JobFollower('j');calls=[];closed=[]
   def connect(cursor):
    calls.append(cursor)
    try:
     if len(calls)==1:yield bad
     else:yield wire(e(1,1,'failed'))
    finally:closed.append(True)
   self.assertEqual(f.follow(connect)['state'],'failed');self.assertEqual(calls,[None,None]);self.assertEqual(len(closed),2)
 def test_eof_connections_close_and_remain_uncertain(self):
  f=JobFollower('j');closed=[]
  def connect(cursor):
   try:yield wire(e(1,1))
   finally:closed.append(True)
  s=f.follow(connect,2);self.assertTrue(s['uncertain']);self.assertEqual(s['state'],'running');self.assertEqual(len(closed),2)
 def test_programmer_error_propagates_and_closes(self):
  f=JobFollower('j');closed=[]
  def connect(cursor):
   try:yield wire(e(1,1));raise RuntimeError('fixture programmer error')
   finally:closed.append(True)
  with self.assertRaises(RuntimeError):f.follow(connect)
  self.assertEqual(len(closed),1)
 def test_transition_matrix_extra_branches(self):
  f=JobFollower('j');self.assertTrue(f.apply(e(1,1,'queued')));self.assertTrue(f.apply(e(2,2,'unknown')));self.assertTrue(f.apply(e(3,3,'running')));before=f.summary()
  with self.assertRaises(ValueError):f.apply(e(4,4,'queued'))
  self.assertEqual(f.summary(),before)
 def test_stop_after_first_terminal_event_in_same_chunk(self):
  class Count(JobFollower):
   def __init__(self):super().__init__('j');self.applied=0
   def apply(self,event):self.applied+=1;return super().apply(event)
  f=Count();f.follow(lambda c:[wire(e(1,1,'canceled'))+wire(e(2,2,'failed'))]);self.assertEqual(f.applied,1)
 def test_nonblank_identifier_and_return_copy(self):
  with self.assertRaises(ValueError):JobFollower('   ')
  f=JobFollower('j');s=f.follow(lambda c:[wire(e(1,1,'canceled'))]);s['state']='running';self.assertEqual(f.summary()['state'],'canceled')
if __name__=='__main__':unittest.main()

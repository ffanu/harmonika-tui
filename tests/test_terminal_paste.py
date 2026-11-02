import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/tools'))
from terminal_paste import PasteFilter,START,END,LIMIT

class Tests(unittest.TestCase):
 def test_finder_path_and_filename_split_every_byte(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'foto café.png';p.touch()
   for text in (str(p),p.name,"'"+str(p)+"'"):
    f=PasteFilter(lambda:{'paths':[p.as_uri()]})
    result=b''.join(f.feed(bytes([b])) for b in START+text.encode()+END)
    self.assertEqual(result,b'\x16')
 def test_normal_code_and_keyboard_unchanged(self):
  f=PasteFilter(lambda:{'paths':[],'text':'other'})
  for data in (b'hello',b'\x1b[A',START+b'print("ok")\n'+END):self.assertEqual(f.feed(data),data)
 def test_stale_clipboard_does_not_replace_different_paste(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.png';p.touch();f=PasteFilter(lambda:{'paths':[str(p)]})
   raw=START+b'a different question'+END
   self.assertEqual(f.feed(raw),raw)
 def test_oversized_paste_bounded_and_preserved(self):
  f=PasteFilter(lambda:self.fail('clipboard lookup for oversized input'))
  raw=START+b'x'*(LIMIT+10)+END
  self.assertEqual(f.feed(raw[:LIMIT+8])+f.feed(raw[LIMIT+8:]),raw)
if __name__=='__main__':unittest.main()

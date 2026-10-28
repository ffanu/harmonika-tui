import importlib.util,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('renderer',Path(__file__).resolve().parents[2]/'backend/video_jobs/renderer.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_trim_exact_ten_seconds(self):
  import av
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);source=root/'source.mp4';dest=root/'result.mp4'
   with av.open(str(source),'w') as out:
    stream=out.add_stream('libx264',rate=24);stream.width=64;stream.height=64;stream.pix_fmt='yuv420p'
    for i in range(243):
     frame=av.VideoFrame(64,64,'rgb24')
     for plane in frame.planes:plane.update(bytes([i%255])*plane.buffer_size)
     for packet in stream.encode(frame):out.mux(packet)
    for packet in stream.encode():out.mux(packet)
   result=m.trim_video(source,dest,10)
   self.assertEqual(result['frames'],240);self.assertEqual(result['duration_seconds'],10);self.assertEqual(result['rendered_duration_seconds'],10.125)
   self.assertFalse(result['audio'])
 def test_corrupt_artifact(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'bad.mp4').write_bytes(b'invalid')
   with self.assertRaises(Exception):m.trim_video(root/'bad.mp4',root/'result.mp4',10)
 def test_resource_and_busy_guard(self):
  for stats,queue,code in [({'system':{'ram_free':0},'devices':[]},{},'system_memory_low'),({'system':{'ram_free':16*1024**3},'devices':[{'vram_free':0}]},{},'gpu_memory_full'),({'system':{'ram_free':16*1024**3},'devices':[{'vram_free':8*1024**3}]},{'queue_running':[1]},'gpu_busy')]:
   with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);(root/'qualification-status.json').write_text('{"port":60887}');(root/'smoke-workflow.json').write_text('{"140:124":{},"140:131":{},"140:129":{},"92":{}}')
    with patch.object(m,'request',side_effect=[stats,queue]):
     with self.assertRaisesRegex(m.RenderError,code):m.render(root,root,'a'*32,{'prompt':'test','duration_seconds':10},lambda *x:None)
if __name__=='__main__':unittest.main()

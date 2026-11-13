"""Geometric coverage and partial-failure regressions for long mockup inspection."""
import importlib.util,io,pathlib,tempfile,unittest
from unittest.mock import patch
from PIL import Image
ROOT=pathlib.Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('mockup_tiles',ROOT/'runtime/tools/visual_media.py');media=importlib.util.module_from_spec(s);s.loader.exec_module(media)

def verified(text):return {'text':text,'model':'qwen3-vl:8b-instruct','engine':'qwen3-vl-8b-rtx-local','gpu_verified':True,'processing_mode':'local_only'}

class TileTests(unittest.TestCase):
    def test_long_and_wide_images_cover_every_scanline_with_overlap(self):
        for w,h in [(1080,5400),(5400,1080),(390,16000)]:
            boxes=media.tile_plan(w,h);self.assertLessEqual(len(boxes),12)
            coverage=[False]*(h if h>w else w)
            for x0,y0,x1,y1 in boxes:
                self.assertTrue(0<x1-x0<=4096 and 0<y1-y0<=4096)
                for i in range(y0,y1) if h>w else range(x0,x1):coverage[i]=True
            self.assertTrue(all(coverage));self.assertEqual(boxes[0][:2],(0,0));self.assertEqual(boxes[-1][2:],(w,h))
    def test_existing_supported_images_stay_one_original_frame(self):
        self.assertEqual(media.tile_plan(2048,1536),[(0,0,2048,1536)])
        for size in [(20000,100),(5000,5000),(0,1)]:
            with self.assertRaises(ValueError):media.tile_plan(*size)
    def invoke(self,failure=False):
        submits=[];polls=[]
        def rpc(path,token,body=None,content_type=None,job_id=None,timeout=None):
            if body is not None:submits.append((path,job_id,body));return {'status':'queued'}
            polls.append(path)
            if failure and len(polls)==2:return {'status':'failed','error':{'code':'gpu_busy'}}
            return {'status':'completed','result':verified('observed panel '+str(len(polls)))}
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'long.png';Image.new('RGB',(1080,5400),'navy').save(p)
            with patch.object(media,'ensure_media_port',return_value=True),patch.object(media,'request',side_effect=rpc),patch.object(media.time,'sleep'):
                value=media.analyze({'path':str(p),'prompt':'Describe visible panels neutrally'},'x'*48)
        return value,submits
    def test_success_has_verified_provenance_all_tiles_and_last_region(self):
        v,calls=self.invoke();self.assertTrue(v['ok']);self.assertTrue(v['coverage_complete']);self.assertEqual(v['tile_count'],3)
        self.assertEqual(len(calls),3);self.assertEqual(len(set(x[1] for x in calls)),3)
        self.assertEqual(v['tiles'][-1]['box'][-1],5400);self.assertIn('observed panel 3',v['text']);self.assertTrue(v['gpu_verified'])
        from email.parser import BytesParser
        from email.policy import default
        import hashlib,json
        for (_,jid,body),tile in zip(calls,v['tiles']):
            boundary=body.split(b'\r\n',1)[0][2:]
            message=BytesParser(policy=default).parsebytes(b'Content-Type: multipart/form-data; boundary="'+boundary+b'"\r\nMIME-Version: 1.0\r\n\r\n'+body)
            parts=list(message.iter_parts());meta=json.loads(parts[0].get_payload(decode=True));png=parts[1].get_payload(decode=True)
            self.assertEqual(meta['job_id'],jid);self.assertEqual(meta['reference_files'][0]['sha256'],hashlib.sha256(png).hexdigest())
            with Image.open(io.BytesIO(png)) as im:
                box=tile['box'];self.assertEqual(im.size,(box[2]-box[0],box[3]-box[1]));self.assertEqual(im.getpixel((10,10)),(0,0,128))
    def test_second_tile_failure_never_claims_whole_image_review(self):
        v,calls=self.invoke(True);self.assertFalse(v['ok']);self.assertFalse(v['coverage_complete']);self.assertTrue(v['analysis_partial'])
        self.assertEqual(v['completed_tiles'],1);self.assertEqual(len(calls),2);self.assertEqual(v['error'],'gpu_busy')
    def test_unverified_model_or_cpu_output_is_not_visual_proof(self):
        for k,value in [('model','other'),('gpu_verified',False),('processing_mode','cloud'),('text','')]:
            result=verified('pixels');result[k]=value;self.assertFalse(media.result_verified(result))

if __name__=='__main__':unittest.main()

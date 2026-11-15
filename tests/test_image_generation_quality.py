import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/tools'))
import image_generation as g


class GenerationGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image=Image.new('RGB',(1024,1024),'green');raw=io.BytesIO();image.save(raw,format='PNG',compress_level=0);cls.raw=raw.getvalue();image.close()

    def run_case(self,observations,fail=None,prompt='One scarlet macaw'):
        class Client:
            calls=0;jobs=[]
            def render(inner,prompt,width,height,deadline):
                inner.calls+=1
                if fail:raise fail
                jid='gw_'+str(inner.calls).zfill(16);inner.jobs.append(jid)
                return self.raw,{'engine':'flux2-klein-4b'},jid
        client=Client();client.jobs=[];seen=[]
        def inspect(args,token):
            seen.append(args['prompt']);value=observations[len(seen)-1]
            return {'ok':True,'coverage_complete':True,'model':'qwen3-vl:8b-instruct','engine':'qwen3-vl-8b-rtx-local','gpu_verified':True,'processing_mode':'local_only','text':json.dumps(value)}
        with tempfile.TemporaryDirectory() as d:
            result=g.generate({'prompt':prompt},d,'synthetic-token',client=client,inspector=inspect)
            files=list(Path(d).glob('*.png'))
            if result['ok']:self.assertEqual(len(files),1);self.assertEqual(files[0].stat().st_mode&0o777,0o600)
            else:self.assertEqual(files,[])
        return result,client.calls,len(seen)

    def test_bad_first_render_gets_one_completed_correction_then_passes(self):
        out,calls,checks=self.run_case([{'visible_heads':2,'visible_torsos':1,'anatomy_issue':'fused_bodies'},{'visible_heads':1,'visible_torsos':1,'anatomy_issue':'none'}])
        self.assertTrue(out['ok']);self.assertEqual((calls,checks),(2,2));self.assertEqual(out['generation_attempts'],2)

    def test_two_failed_attempts_never_publish_an_image(self):
        bad={'visible_heads':2,'visible_torsos':1,'anatomy_issue':'duplicate_head'}
        out,calls,_=self.run_case([bad,bad]);self.assertFalse(out['ok']);self.assertEqual(calls,2);self.assertEqual(out['error'],'image_visual_quality_failed')

    def test_uncertain_inspection_never_claims_success_or_submits_again(self):
        out,calls,_=self.run_case([{'visible_heads':-1,'visible_torsos':-1,'anatomy_issue':'uncertain'}]);self.assertFalse(out['ok']);self.assertEqual(calls,1)

    def test_unknown_submission_is_not_retried(self):
        out,calls,_=self.run_case([],g.Pending('image_submission_uncertain'));self.assertEqual(calls,1);self.assertTrue(out['uncertain'])

    def test_completed_gateway_failure_is_not_a_semantic_retry(self):
        out,calls,_=self.run_case([],g.Rejected('image_generation_failed'));self.assertEqual(calls,1);self.assertFalse(out['ok'])

    def test_expired_deadline_cannot_submit_a_job(self):
        client=object.__new__(g.Gateway)
        with patch.object(g.time,'monotonic',return_value=10),patch.object(client,'request') as request:
            with self.assertRaisesRegex(g.Rejected,'image_quality_deadline_exceeded'):
                client.render('one bird',2048,2048,9)
            request.assert_not_called()

    def test_fantasy_remains_unmodified_without_false_anatomy_approval(self):
        out,calls,checks=self.run_case([],prompt='fantasy two-headed bird');self.assertTrue(out['ok']);self.assertEqual(checks,0);self.assertEqual(out['quality_verification'],'visual_review_required')


if __name__=='__main__':unittest.main()

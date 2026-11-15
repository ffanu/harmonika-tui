"""Exercise the public OCR boundary, including false provenance and malformed pages."""
import copy, importlib.util, io, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
PATH=Path(__file__).resolve().parents[1]/'runtime/tools/shared_ocr.py'
spec=importlib.util.spec_from_file_location('ocr_contract',PATH);ocr=importlib.util.module_from_spec(spec);spec.loader.exec_module(ocr)

def valid():
    return {'ok':True,'status':'completed','engine':'qwen3-vl-8b-rtx-local','processing_mode':'local_only',
        'text':'Synthetic text','text_detected':'Synthetic text','page_count':1,
        'pages':[{'page':1,'text':'Synthetic text','engine':'qwen3-vl-8b-rtx-local','device':'windows_rtx','gpu_verified':True}],
        'primary_device':'windows_rtx','fallback_used':False,'document_authenticity_verified':False}

class ContractTests(unittest.TestCase):
    def invoke(self,value,pdf=False):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return json.dumps(value).encode()
        class Opener:
            def open(self,*args,**kwargs):return Response()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/('fixture.pdf' if pdf else 'fixture.png')
            if pdf:p.write_bytes(b'%PDF-1.7\nsynthetic mocked response')
            else:Image.new('RGB',(8,8),'white').save(p)
            with patch.object(ocr,'auth_headers',return_value={}),patch.object(ocr.urllib.request,'build_opener',return_value=Opener()):return ocr.extract_file(str(p))
    def test_valid_verified_gpu_is_accepted(self):self.assertTrue(self.invoke(valid())['ok'])
    def test_false_gpu_claim_is_rejected(self):
        v=valid();v['pages'][0]['gpu_verified']=False;self.assertEqual(self.invoke(v).get('error'),'ocr_invalid_response')
    def test_unknown_page_engine_is_rejected(self):
        v=valid();v['pages'][0]['engine']='unverified-cloud';self.assertEqual(self.invoke(v).get('error'),'ocr_invalid_response')
    def test_ocr_never_certifies_document_authenticity(self):
        v=valid();v['document_authenticity_verified']=True;self.assertEqual(self.invoke(v).get('error'),'ocr_invalid_response')
    def test_missing_or_duplicate_pdf_page_is_rejected(self):
        for pages,count in [([],1),([valid()['pages'][0]],2),([valid()['pages'][0]]*2,2)]:
            v=valid();v.update(pages=pages,page_count=count);self.assertEqual(self.invoke(v).get('error'),'ocr_invalid_response')
    def test_cpu_fallback_cannot_be_labelled_gpu(self):
        v=valid();v['pages'][0].update(engine='paddleocr-v5-local-cpu',device='linux_cpu',gpu_verified=False)
        self.assertEqual(self.invoke(v).get('error'),'ocr_invalid_response')
    def test_conflicting_text_alias_is_rejected(self):
        v=valid();v['text_detected']='Different invented data';self.assertEqual(self.invoke(v).get('error'),'ocr_invalid_response')
    def test_unknown_page_metadata_is_not_exposed(self):
        v=valid();v['pages'][0]['internal_secret']='synthetic-internal-value'
        result=self.invoke(v);self.assertTrue(result['ok']);self.assertNotIn('internal_secret',result['pages'][0])
    def test_missing_page_text_cannot_be_hidden_by_combined_text(self):
        v=valid();v['text']='Invented combined text';v['text_detected']=v['text']
        self.assertEqual(self.invoke(v).get('error'),'ocr_invalid_response')
    def test_valid_cpu_and_mixed_page_provenance_are_accepted(self):
        v=valid();cpu={'page':1,'text':'Synthetic text','engine':'paddleocr-v5-local-cpu','device':'linux_cpu','gpu_verified':False}
        v.update(engine=cpu['engine'],pages=[cpu],fallback_used=True);self.assertTrue(self.invoke(v)['ok'])
        v=valid();v['pages'][0]['page']=2;v.update(engine='mixed_local',pages=[cpu,v['pages'][0]],page_count=2,fallback_used=True,text='Synthetic text\n\nSynthetic text',text_detected='Synthetic text\n\nSynthetic text')
        self.assertTrue(self.invoke(v,pdf=True)['ok'])


class QualityFallbackTests(unittest.TestCase):
    def cpu(self):
        v=valid();v.update(engine='paddleocr-v5-local-cpu',fallback_used=True)
        v['pages'][0].update(engine=v['engine'],device='linux_cpu',gpu_verified=False)
        return v
    def test_transient_fallback_recovers_multilingual_text_and_marks_disagreement(self):
        cpu=self.cpu();gpu=valid();gpu['text']='مرحبا بالعالم';gpu['text_detected']=gpu['text'];gpu['pages'][0]['text']=gpu['text']
        with patch.object(ocr.time,'sleep'):
            selected,meta=ocr.prefer_rtx(cpu,False,lambda:gpu)
        self.assertEqual(selected['text'],'مرحبا بالعالم');self.assertTrue(meta['recovered_from_cpu_fallback']);self.assertTrue(meta['cross_engine_difference']);self.assertEqual(meta['quality_attempts'],2)
    def test_failed_recheck_preserves_honest_cpu_result(self):
        def unavailable():raise TimeoutError('synthetic')
        cpu=self.cpu()
        with patch.object(ocr.time,'sleep'):
            selected,meta=ocr.prefer_rtx(cpu,False,unavailable)
        self.assertIs(selected,cpu);self.assertTrue(meta['quality_recheck_failed']);self.assertTrue(selected['fallback_used'])
    def test_pdf_batches_are_never_replayed(self):
        cpu=self.cpu()
        def no_call():self.fail('PDF must not be replayed')
        selected,meta=ocr.prefer_rtx(cpu,True,no_call);self.assertIs(selected,cpu);self.assertEqual(meta['quality_attempts'],1)
    def test_empty_rtx_recheck_cannot_erase_valid_cpu_text(self):
        cpu=self.cpu();empty=valid();empty.update(text='',text_detected='');empty['pages'][0]['text']=''
        with patch.object(ocr.time,'sleep'):
            selected,meta=ocr.prefer_rtx(cpu,False,lambda:empty)
        self.assertIs(selected,cpu);self.assertTrue(meta['quality_recheck_failed'])
    def test_unverified_retry_cannot_replace_valid_cpu(self):
        cpu=self.cpu();bad=valid();bad['pages'][0]['gpu_verified']=False
        with patch.object(ocr.time,'sleep'):
            selected,meta=ocr.prefer_rtx(cpu,False,lambda:bad)
        self.assertIs(selected,cpu);self.assertTrue(meta['quality_recheck_failed'])

if __name__=='__main__':unittest.main()

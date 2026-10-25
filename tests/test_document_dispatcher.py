"""Observable local document behavior and safety invariants."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/harmonika-documents/scripts'
sys.path.insert(0, str(SCRIPTS))
import documents as d

def setUpModule():
    if os.environ.get('HARMONIKA_DOCUMENTS_REQUIRE_ALL') == '1' and not all(d.capabilities().values()):
        raise RuntimeError('Document verification requires all four format dependencies; skips refused')

class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='harmonika-documents-')
        self.root = Path(self.tmp.name).resolve()
    def tearDown(self): self.tmp.cleanup()
    def pdf(self, name='original.pdf', text='HARMONIKA PDF 4821'):
        if not d.capabilities()['.pdf']: self.skipTest('PDF dependencies missing')
        target = self.root / name
        d.create(target, {'title':'Local report', 'paragraphs':[text, '<b>literal markup</b>']})
        return target
    def test_capability_operations_use_only_required_dependencies(self):
        from unittest.mock import patch
        with patch.object(d, 'available', side_effect=lambda name:name == 'reportlab'):
            self.assertTrue(d.detailed_capabilities()['formats']['.pdf']['operations']['create'])
            self.assertFalse(d.detailed_capabilities()['formats']['.pdf']['operations']['inspect'])
            d.dependency('.pdf', 'create')
            with self.assertRaises(ValueError):d.dependency('.pdf', 'inspect')
        with patch.object(d, 'available', side_effect=lambda name:name == 'pypdf'):
            self.assertTrue(d.detailed_capabilities()['formats']['.pdf']['operations']['inspect'])
            d.dependency('.pdf', 'edit', {'rotation':90})
            with self.assertRaises(ValueError):d.dependency('.pdf', 'edit', {'watermark':'DRAFT'})
    def test_text_character_limit_is_reported(self):
        source=self.pdf(text='ABCDEF '*500)
        longfile=self.root/'long.pdf'
        d.create(longfile, {'paragraphs':['ABCDEF '*500]*12})
        result=d.inspect(longfile)
        self.assertEqual(len(result['text']),30000)
        self.assertTrue(result['truncated'])
    def test_pdf_edit_removes_annotations_and_actions(self):
        source=self.pdf()
        from pypdf import PdfReader, PdfWriter
        from pypdf.annotations import Link
        from pypdf.generic import NameObject, DictionaryObject, TextStringObject
        writer=PdfWriter(clone_from=source)
        writer.add_annotation(0, Link(rect=(10,10,100,100), url='https://example.invalid/untrusted'))
        writer.pages[0][NameObject('/AA')]=DictionaryObject({NameObject('/O'):DictionaryObject({NameObject('/S'):NameObject('/JavaScript'), NameObject('/JS'):TextStringObject('untrusted fixture')})})
        writer.add_js('untrusted fixture')
        active=self.root/'active.pdf';writer.write(active)
        output=self.root/'sanitized.pdf';d.edit(active,output,{'rotation':90},str(self.root))
        result=PdfReader(output)
        self.assertNotIn('/Annots',result.pages[0]);self.assertNotIn('/AA',result.pages[0])
        for key in ('/OpenAction','/Names','/AcroForm','/AA'):
            self.assertNotIn(key,result.trailer['/Root'])
        self.assertIn('HARMONIKA',result.pages[0].extract_text())
    def test_pdf_fonts_are_builtin_and_unsupported_glyphs_fail(self):
        file=self.pdf()
        self.assertNotIn(b'/FontFile2',file.read_bytes())
        with self.assertRaisesRegex(ValueError,'unsupported_glyphs'):
            d.create(self.root/'emoji.pdf',{'paragraphs':['unsupported \U0001f600']})
        self.assertFalse((self.root/'emoji.pdf').exists())
        cjk=self.root/'cjk.pdf';d.create(cjk,{'paragraphs':['测试文档'],'cjk':True})
        self.assertIn('测试文档',d.inspect(cjk)['text'])
    def test_publish_failure_leaves_no_output_or_pending_file(self):
        from unittest.mock import patch
        output=self.root/'not-published.pdf'
        with patch.object(d.os,'link',side_effect=OSError('publication failed')):
            with self.assertRaises(OSError):d._save(output,lambda out:out.write(b'content'),'.pdf')
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob('*.pending')),[])
    def test_pdf_aggregate_input_budget(self):
        source=self.pdf();extras=[]
        for i in range(4):
            file=self.root/('extra'+str(i)+'.pdf')
            with file.open('wb') as out:out.write(b'%PDF-');out.truncate(10*1024*1024)
            extras.append(str(file))
        with self.assertRaisesRegex(ValueError,'aggregate PDF edit inputs'):
            d.edit(source,self.root/'too-big.pdf',{'append_files':extras},str(self.root))
    def test_isolated_cli_ignores_pythonpath_payload(self):
        source=self.pdf();attack=self.root/'attack';attack.mkdir();marker=self.root/'injected.txt'
        (attack/'pypdf.py').write_text('from pathlib import Path;Path('+repr(str(marker))+').write_text("executed");raise RuntimeError("injected")')
        env=dict(os.environ,PYTHONPATH=str(attack))
        result=subprocess.run([sys.executable,'-I',str(SCRIPTS/'documents.py'),'inspect','--root',str(self.root),'--input',str(source)],env=env,capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(marker.exists());self.assertIn('4821',json.loads(result.stdout)['text'])
    def test_pdf_worker_does_not_load_user_reportlab_config(self):
        if not d.capabilities()['.pdf']:self.skipTest('PDF dependencies missing')
        attack=self.root/'profile';attack.mkdir();marker=self.root/'reportlab-injected.txt'
        (attack/'.reportlab_mods').write_text('from pathlib import Path;Path('+repr(str(marker))+').write_text("executed")')
        spec=self.root/'create.json';spec.write_text(json.dumps({'paragraphs':['SAFE LOCAL PDF']}))
        output=self.root/'clean-home.pdf'
        result=subprocess.run([sys.executable,'-I',str(SCRIPTS/'documents.py'),'create','--root',str(self.root),'--output',str(output),'--data',str(spec)],env=dict(os.environ,HOME=str(attack)),capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(marker.exists())
        self.assertIn('SAFE LOCAL PDF',d.inspect(output)['text'])
    def test_public_cli_cannot_bypass_parent_deadline(self):
        source=self.pdf()
        result=subprocess.run([sys.executable,'-I',str(SCRIPTS/'documents.py'),'--pdf-worker','inspect','--root',str(self.root),'--input',str(source)],capture_output=True,text=True,timeout=40)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,'')
    def test_malformed_registry_is_structured(self):
        import shutil
        skill=self.root/'skill';shutil.copytree(SCRIPTS.parent,skill,ignore=shutil.ignore_patterns('__pycache__'))
        (skill/'manifest.yaml').write_text('{broken')
        result=subprocess.run([sys.executable,'-I',str(skill/'scripts/documents.py'),'capabilities'],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,2);self.assertEqual(json.loads(result.stderr)['code'],'invalid_document_manifest')
    def test_system_tmp_alias_is_allowed_without_arbitrary_symlink_escape(self):
        if not Path('/tmp').is_symlink():self.skipTest('system tmp alias absent')
        import tempfile
        with tempfile.TemporaryDirectory(dir='/tmp',prefix='harmonika-alias-') as folder:
            target=Path(folder)/'file.pdf'
            self.assertEqual(d.scoped(folder,str(target)),target.resolve())
    def test_manifest_and_dependencies_match_packaging(self):
        skill = SCRIPTS.parent
        lock = SCRIPTS.parents[2] / 'runtime/documents-requirements.lock'
        self.assertEqual((skill/'requirements.txt').read_text(), lock.read_text())
        self.assertEqual(d.REGISTRY['schema_version'], 1)
        self.assertTrue(all(not value['enabled'] for value in d.REGISTRY['reserved_formats'].values()))
    def test_pdf_create_read_literal_text(self):
        result = d.inspect(self.pdf())
        self.assertIn('HARMONIKA PDF 4821', result['text'])
        self.assertIn('<b>literal markup</b>', result['text'])
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(result['ocr'])
    def test_pdf_edit_merge_rotate_watermark_preserves_original(self):
        first = self.pdf(); second = self.pdf('other.pdf', 'APPEND 5932')
        before = first.read_bytes(); output = self.root/'edited.pdf'
        d.edit(first, output, {'pages':[1], 'rotation':90,
                             'append_files':[str(second)], 'watermark':'DRAFT'}, str(self.root))
        from pypdf import PdfReader
        doc = PdfReader(output)
        self.assertEqual(len(doc.pages), 2)
        self.assertEqual(doc.pages[0].rotation, 90)
        self.assertIn('DRAFT', doc.pages[0].extract_text())
        self.assertIn('APPEND 5932', doc.pages[1].extract_text())
        self.assertEqual(first.read_bytes(), before)
    def test_invalid_edit_no_output(self):
        source = self.pdf(); output = self.root/'bad.pdf'
        for spec in ({'pages':[0]}, {'rotation':45}, {'bogus':True}, {'watermark':''}, {'pages':[True]}):
            with self.assertRaises(ValueError): d.edit(source, output, spec, str(self.root))
            self.assertFalse(output.exists())
    def test_append_outside_and_symlink_rejected(self):
        source = self.pdf(); output = self.root/'bad.pdf'
        (self.root/'link.pdf').symlink_to(source)
        for value in (str(self.root.parent/'other.pdf'), str(self.root/'link.pdf')):
            with self.assertRaises(ValueError): d.edit(source, output, {'append_files':[value]}, str(self.root))
            self.assertFalse(output.exists())
    def test_scan_is_degraded_encrypted_and_corrupt_refused(self):
        if not d.available('pypdf'): self.skipTest('pypdf absent')
        from pypdf import PdfWriter
        file = self.root/'blank.pdf'
        writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.write(file)
        result=d.inspect(file)
        self.assertEqual(result['status'],'degraded');self.assertEqual(result['error'],'pdf_needs_ocr')
        writer.encrypt('test-only-password'); protected=self.root/'encrypted.pdf';writer.write(protected)
        with self.assertRaisesRegex(ValueError,'pdf_encrypted'):d.inspect(protected)
        bad=self.root/'bad.pdf';bad.write_bytes(b'not PDF')
        with self.assertRaisesRegex(ValueError,'invalid_pdf'):d.inspect(bad)
    def test_pdf_bounds_and_page_selection(self):
        if not d.available('pypdf'):self.skipTest('pypdf absent')
        from pypdf import PdfWriter, PdfReader
        source=self.pdf(); writer=PdfWriter()
        page=PdfReader(source).pages[0]
        for i in range(22):writer.add_page(page)
        many=self.root/'many.pdf';writer.write(many)
        result=d.inspect(many)
        self.assertEqual(result['pages_processed'],20);self.assertTrue(result['truncated'])
        selected=self.root/'selected.pdf';d.edit(many, selected, {'pages':[22,1]},str(self.root))
        self.assertEqual(len(PdfReader(selected).pages),2)
        large=self.root/'large.pdf'
        with large.open('wb') as out:out.write(b'%PDF-');out.truncate(10*1024*1024+1)
        with self.assertRaisesRegex(ValueError,'file_too_large'):d.inspect(large)
    def test_existing_output_not_truncated(self):
        source=self.pdf();original=source.read_bytes()
        with self.assertRaisesRegex(ValueError,'Output exists'):d.edit(source,source,{'rotation':90},str(self.root))
        self.assertEqual(source.read_bytes(), original)
    def test_unsupported_format_and_operation(self):
        with self.assertRaisesRegex(ValueError,'unsupported_format'):d.create(self.root/'book.epub',{})
        with self.assertRaisesRegex(ValueError,'unsupported_operation'):d.edit(self.root/'a.docx',self.root/'b.docx',{},str(self.root))
    def test_failure_render_publishes_nothing(self):
        output=self.root/'none.pdf'
        def fail(handle):handle.write(b'partial');raise ValueError('failed')
        with self.assertRaises(ValueError):d._save(output, fail, '.pdf')
        self.assertFalse(output.exists())
    def test_cli_and_structured_errors(self):
        source=self.pdf()
        command=[sys.executable,str(SCRIPTS/'documents.py'),'inspect','--root',str(self.root),'--input',str(source)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('4821',json.loads(result.stdout)['text'])
        source.write_bytes(b'bad PDF')
        result=subprocess.run(command,capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stderr)['code'],'invalid_pdf')
        self.assertEqual(result.stdout,'')

if __name__=='__main__':unittest.main(verbosity=2)

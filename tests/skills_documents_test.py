import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/harmonika-documents/scripts/documents.py'
spec = importlib.util.spec_from_file_location('documents', SCRIPT)
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)

class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='harmonika-skills-')
        self.root = Path(self.temp.name).resolve()
    def tearDown(self):
        self.temp.cleanup()
    def test_scoped_paths(self):
        self.assertEqual(docs.scoped(str(self.root), str(self.root/'a.docx')), self.root/'a.docx')
        for value in [str(self.root.parent/'outside.docx'), str(self.root/'.ssh'/'file'), 'relative.docx']:
            with self.assertRaises(ValueError): docs.scoped(str(self.root), value)
        (self.root/'link').symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError): docs.scoped(str(self.root), str(self.root/'link'/'outside'))
    def test_missing_dependency_is_explicit(self):
        with patch.object(docs, 'capabilities', return_value={'.docx':False,'.xlsx':False,'.pptx':False}):
            with self.assertRaisesRegex(ValueError,'Unsupported: missing optional'):
                docs.create(self.root/'a.docx', {'paragraphs':['hello']})
        self.assertFalse((self.root/'a.docx').exists())
    def test_corrupt_document_refused_without_output(self):
        target = self.root/'broken.docx'
        target.write_text('not a zip file')
        if docs.capabilities()['.docx']:
            with self.assertRaises(Exception): docs.inspect(target)
        self.assertEqual(target.read_text(), 'not a zip file')
    def test_capabilities_are_actual_booleans(self):
        self.assertEqual(set(docs.capabilities()), {'.docx','.xlsx','.pptx','.pdf'})
        self.assertTrue(all(isinstance(x,bool) for x in docs.capabilities().values()))
    def roundtrip(self, ext, data, expected):
        if not docs.capabilities()[ext]: self.skipTest('optional module absent: '+docs.MODULES[ext])
        target=self.root/('actual'+ext)
        result=docs.create(target,data)
        self.assertGreater(result['bytes'],0)
        output=docs.inspect(target)
        self.assertIn(expected,json.dumps(output))
        original=target.read_bytes()
        with self.assertRaisesRegex(ValueError,'Output exists'):docs.create(target,data)
        self.assertEqual(target.read_bytes(),original)
    def test_docx_roundtrip(self):
        self.roundtrip('.docx', {'paragraphs':['Harmonika test']}, 'Harmonika test')
    def test_xlsx_roundtrip(self):
        self.roundtrip('.xlsx', {'sheets':[{'name':'Data','rows':[['Name','Value'],['Harmonika test','=1+1']]}]}, 'Harmonika test')
        if docs.capabilities()['.xlsx']:
            from openpyxl import load_workbook
            book=load_workbook(self.root/'actual.xlsx')
            self.assertEqual(book.active['B2'].data_type,'s')
            book.close()
    def test_pptx_roundtrip(self):
        self.roundtrip('.pptx', {'slides':[{'title':'Harmonika test','body':['Evidence']} ]}, 'Harmonika test')

if __name__ == '__main__': unittest.main(verbosity=2)

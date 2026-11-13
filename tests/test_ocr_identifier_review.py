import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1]/'runtime/tools/shared_ocr.py'
spec = importlib.util.spec_from_file_location('ocr_review_tests', PATH)
ocr = importlib.util.module_from_spec(spec); spec.loader.exec_module(ocr)


class IdentifierReviewTests(unittest.TestCase):
    def test_lowercase_l_is_not_silently_accepted_as_capital_i(self):
        text, count = ocr.guard_identifiers('Nomor tiket: HAl-4827')
        self.assertEqual(count, 1)
        self.assertIn('HA{I/l/1}-4827', text)
        self.assertIn('OCR mentah: HAl-4827', text)
        self.assertNotIn('HAI-4827', text)

    def test_existing_o_zero_review_is_preserved_and_not_nested(self):
        source = '[kurang pasti: A{O/0}-1{O/0}89; OCR mentah: A0-1O89; verifikasi O/0 pada kode]'
        text, count = ocr.guard_identifiers(source)
        self.assertEqual(count, 1)
        self.assertIn('A{O/0}-{I/l/1}{O/0}89', text)
        self.assertIn('OCR mentah: A0-1O89', text)
        self.assertEqual(text.count('[kurang pasti:'), 1)
        self.assertEqual(ocr.guard_identifiers(text), (text, 0))

    def test_dates_amounts_and_normal_words_remain_unchanged(self):
        source = '13/11/2026 Rp 675.000 BELUM DIBAYAR Wi-Fi6 Test-only'
        self.assertEqual(ocr.guard_identifiers(source), (source, 0))

    def test_page_text_and_top_level_both_preserve_review(self):
        out = {'ok': True, 'text': 'HAl-4827', 'pages': [{'text': 'HAl-4827'}],
               'identifier_review_count': 2, 'warnings': [], 'review_required': False,
               'engine': 'qwen3-vl-8b-rtx-local', 'fallback_used': False}
        result = ocr.apply_identifier_review(out)
        self.assertTrue(result['review_required'])
        self.assertEqual(result['identifier_review_count'], 3)
        self.assertTrue(result['pages'][0]['review_required'])
        self.assertEqual(result['engine'], out['engine'])
        self.assertFalse(result['fallback_used'])

    def test_overlapping_ambiguity_does_not_count_one_identifier_twice(self):
        source = '[kurang pasti: A{O/0}-1{O/0}89; OCR mentah: A0-1O89; verifikasi O/0 pada kode]'
        out = {'text': source, 'identifier_review_count': 1}
        result = ocr.apply_identifier_review(out)
        self.assertEqual(result['identifier_review_count'], 1)
        self.assertEqual(result['identifier_review_count_i1l'], 1)
        self.assertIn('A{O/0}-{I/l/1}{O/0}89', result['text'])

    def test_text_aliases_are_consistent_and_boolean_detection_stays_boolean(self):
        source = '[kurang pasti: A{O/0}-1{O/0}89; OCR mentah: A0-1O89; verifikasi O/0 pada kode]'
        out = {'text': source, 'text_detected': source, 'pages': [{'text': source, 'text_detected': source}], 'identifier_review_count': 1}
        result = ocr.apply_identifier_review(out)
        self.assertEqual(result['text'], result['text_detected'])
        self.assertEqual(result['text'], result['pages'][0]['text'])
        self.assertEqual(result['text'], result['pages'][0]['text_detected'])
        self.assertEqual(result['identifier_review_count'], 1)
        self.assertIs(ocr.apply_identifier_review({'text': 'plain', 'text_detected': True})['text_detected'], True)

    def test_guard_is_idempotent_for_repeated_rendering(self):
        text, _ = ocr.guard_identifiers('INV-1007')
        self.assertEqual(ocr.guard_identifiers(text), (text, 0))


if __name__ == '__main__':
    unittest.main()

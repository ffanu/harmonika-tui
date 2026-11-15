import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report import parse_csv,summary,ReportError
class Public(unittest.TestCase):
 def test_decimal(self):self.assertEqual(summary(parse_csv('invoice_id,project,amount,status\na,one,0.10,paid\nb,one,0.20,paid\n'))[0]['net_total'],'0.30')
 def test_bad_amount(self):
  with self.assertRaises(ReportError):parse_csv('invoice_id,project,amount,status\na,one,NaN,paid\n')
 def test_scopes(self):self.assertEqual(len(parse_csv('invoice_id,project,amount,status\na,one,1,paid\na,two,2,paid\n')),2)

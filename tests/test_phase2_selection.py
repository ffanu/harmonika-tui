import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.coding_runtime.catalog import SKILLS,BASE_SKILLS,PHASE2_SKILLS
from backend.coding_runtime.tool_selection import *
class Selection(unittest.TestCase):
    def test_old_payload_stays18(self):self.assertEqual(len(definitions(select_initial([{'content':'hello'}],BASE_SKILLS),BASE_SKILLS)),18)
    def test_every_task_stays_within32(self):
        for text in ('buat project endpoint react','debug breakpoint baris10','install paket pip offline','refactor rename fungsi ekstrak','type check format build typescript test coverage','hello'):
            selected=select_initial([{'content':text}],SKILLS);self.assertLessEqual(len(definitions(selected,SKILLS)),32);self.assertTrue(BASE_SKILLS<=selected)
    def test_debug_and_refactor_naturally_selected(self):
        self.assertIn('debug.attach',select_initial([{'content':'debug breakpoint'}],SKILLS))
        self.assertIn('refactor.extract',select_initial([{'content':'ekstrak fungsi rename'}],SKILLS))
    def test_discovery_has_all_optional_ids(self):
        defs=definitions(select_initial([],SKILLS),SKILLS);m=next(x for x in defs if x['function']['name']==META_NAME)
        self.assertEqual(set(m['function']['parameters']['properties']['skills']['items']['enum']),PHASE2_SKILLS)
    def test_previous_calls_remain_declared(self):
        selected=discover({'skills':['debug.attach']},{'scaffold.project','code.read'},SKILLS)
        self.assertTrue({'debug.attach','scaffold.project'}<=selected)
    def test_unknown_or_disabled_not_exposed(self):
        for data in ({'skills':['secret.get']},{'skills':['debug.attach']},{'skills':'debug.attach'}):
            with self.assertRaises(ValueError):discover(data,set(),BASE_SKILLS)
    def test_excess_budget_rejected(self):
        with self.assertRaises(ValueError):discover({'skills':list(PHASE2_SKILLS)},set(),SKILLS)

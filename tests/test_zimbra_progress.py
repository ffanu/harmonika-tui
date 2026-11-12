import unittest
from backend.zimbra.progress import ProgressBudget


class ProgressTests(unittest.TestCase):
    def test_repeated_step_starts_cannot_loop_forever(self):
        b=ProgressBudget(3)
        self.assertFalse(b.observe({'type':'step_start'}));self.assertFalse(b.observe({'type':'step_start'}));self.assertTrue(b.observe({'type':'step_start'}))
        r=b.interruption();self.assertFalse(r['safe_to_retry']);self.assertFalse(r['remote_operation_cancelled'])

    def test_completed_tool_resets_budget_and_one_long_step_is_not_a_loop(self):
        b=ProgressBudget(3)
        for _ in range(2):self.assertFalse(b.observe({'type':'step_start'}))
        self.assertFalse(b.observe({'type':'tool_use','part':{'state':{'status':'completed'}}}))
        self.assertFalse(b.observe({'type':'step_start'}));self.assertEqual(b.completed_tools,1)


if __name__=='__main__':unittest.main()

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('planner_patch', Path(__file__).resolve().parents[1]/'scripts/patch-planner-firewall.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PlannerPatch(unittest.TestCase):
    def test_exact_known_anchors_only_and_idempotency(self):
        source = """const {compactPayload}=require('./planner-context');
if(!ajv.validate(tool.parameters,args))throw Error('invalid_tool_arguments');
const cleanContent=sanitizeIdentity(value.content);
emit(code==='tool_input_too_large'?413:code==='tool_engine_busy'?429:503,
message:code==='tool_input_too_large'?'Input exceeds safe planner budget; shorten oversized tool arguments.':'Tool decision engine unavailable or returned invalid output.'
"""
        result = module.patch(source)
        self.assertIn('assertToolRequest(c.name,args)', result)
        self.assertIn('sanitizeIdentity(scrubText(value.content))', result)
        self.assertIn("invalid_tool_history'?400", result)
        self.assertEqual(module.patch(result), result)
        for broken in ('', source.replace('compactPayload', 'differentBoundary'), source+source):
            with self.assertRaisesRegex(ValueError, 'planner_baseline_changed'):
                module.patch(broken)

if __name__ == '__main__':
    unittest.main()

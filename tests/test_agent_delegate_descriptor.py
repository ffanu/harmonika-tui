import importlib.util
from pathlib import Path
import unittest
from backend.engine_registry.discovery import _static_validate, _validate_metadata
from backend.engine_registry.schema import validate

P = Path(__file__).resolve().parents[1]/'backend/agent_delegate/skill.py'
SPEC = importlib.util.spec_from_file_location('delegate_descriptor', P)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

class DelegateDescriptorContract(unittest.TestCase):
    def test_descriptor_accepted_by_actual_discovery_boundary(self):
        self.assertIsNone(_static_validate(P.read_bytes()))
        self.assertIsNone(_validate_metadata(M.GetSkillIdentity(), M.GetContextRequirements()))

    def test_submit_and_reconciliation_are_valid_registry_inputs(self):
        schema=M.GetSkillIdentity()['input_schema']
        for payload in [
            {'action':'submit','task':'17*19','request_id':'request-fixed-123456','provider':'auto','timeout_seconds':120},
            {'action':'status','job_id':'dg_'+'a'*32},
            {'action':'status','request_id':'request-fixed-123456'},
        ]: validate(payload,schema)

if __name__=='__main__':unittest.main()

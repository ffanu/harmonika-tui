#!/usr/bin/env python3
"""Prepare the internal task contract; preserve the OpenAI-compatible client wire."""
import argparse
from pathlib import Path


def patch(source):
    if "require('./agent-controller.cjs')" in source:
        if 'agentController.check(value,data)' not in source:raise ValueError('partial_agent_patch')
        return source
    replacements=[
        ("const longDecision=require('./long-decision.cjs');", "const longDecision=require('./long-decision.cjs');\nconst agentController=require('./agent-controller.cjs');"),
        ('function validateDecision(value,data){','function validateRawDecision(value,data){'),
        ("const {compactPayload}=require('./planner-context');", "function validateDecision(value,data){\n const message=validateRawDecision(value,data);\n const state=agentController.check(value,data);\n console.log(JSON.stringify({event:'agent_task_state',...state}));\n return message;\n}\n\nconst {compactPayload}=require('./planner-context');"),
        (' const instructions=', ' const instructions=agentController.instructions(prepared.data)+'),
        ("  fs.writeFileSync(dir+'/schema.json'", "  Object.assign(schema,agentController.extendSchema(schema,prepared.data));\n  fs.writeFileSync(dir+'/schema.json'"),
    ]
    for old,new in replacements:
        if source.count(old)!=1:raise ValueError('agent_controller_anchor_changed')
        source=source.replace(old,new,1)
    return source


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.write_text(patch(a.source.read_text()));a.output.chmod(0o600)

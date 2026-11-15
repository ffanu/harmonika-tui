#!/usr/bin/env python3
"""Exact-anchor patch for the verified decision-recovery baseline."""
import argparse
from pathlib import Path


def patch(source):
    if "require('./long-decision.cjs')" in source:
        if 'longDecision.run(' not in source:raise ValueError('partial_long_transport_patch')
        return source
    before="  child.stdin.end("
    start=source.index(before)+len(before)
    end=source.index(');\n',start)
    expression=source[start:end]
    tail='+JSON.stringify(prepared.data)'
    if not expression.endswith(tail):raise ValueError('decision_prompt_anchor_changed')
    instructions=expression[:-len(tail)]
    source=source[:start]+'prompt'+source[end:]
    replacements=[
        ("const decisionRecovery=require('./decision-recovery.cjs');", "const decisionRecovery=require('./decision-recovery.cjs');\nconst longDecision=require('./long-decision.cjs');"),
        (' const remoteInstructions=remoteWorkflow.instructions();', ' const remoteInstructions=remoteWorkflow.instructions();\n const instructions='+instructions+';\n const payload=JSON.stringify(prepared.data);\n const prompt=instructions+payload;'),
        ("  const args=['exec'", "  if(longDecision.needsHistory(prompt)){\n   console.warn(JSON.stringify({event:'planner_long_transport',payload_chars:payload.length,payload_bytes:Buffer.byteLength(payload,'utf8'),ephemeral:true}));\n   longDecision.run({instructions,payload,schema:decisionRecovery.constrainSchema(schema,prepared.data),capacity,cwd:dir})\n    .then(raw=>resolve(decisionRecovery.parseResult(raw,validateDecision,prepared.data)))\n    .catch(e=>reject(decisionRecovery.transportError(e)))\n    .finally(()=>{active=Math.max(0,active-1);try{fs.rmSync(dir,{recursive:true,force:true});}catch(_){}});\n   return;\n  }\n  const args=['exec'"),
        ("code:code==='tool_input_too_large'?'invalid_tool_arguments':code", "code:code==='tool_input_too_large'?'context_length_exceeded':code"),
        ("'Input exceeds safe planner budget; shorten oversized tool arguments.'", "'Conversation exceeds the supported context capacity.'"),
        ("type:'server_error'", "type:code==='tool_input_too_large'?'invalid_request_error':'server_error'"),
    ]
    for old,new in replacements:
        if source.count(old)!=1:raise ValueError('long_transport_anchor_changed')
        source=source.replace(old,new,1)
    return source


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.write_text(patch(a.source.read_text()));a.output.chmod(0o600)

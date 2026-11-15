#!/usr/bin/env python3
"""Prepare an exact-anchor planner patch; does not activate or restart services."""
import argparse
from pathlib import Path


def patch(source):
    if "require('./decision-recovery.cjs')" in source:
        if 'decisionRecovery.chooseDecision(plan,data' not in source:
            raise ValueError('partial_recovery_patch')
        return source
    replacements = [
        ("let active=0;", "const decisionRecovery=require('./decision-recovery.cjs');\nlet active=0;"),
        ('function plan(data){', "function plan(data,repair=''){"),
        ('arguments must be JSON objects matching each function parameters schema.', 'arguments must be JSON-encoded object strings matching each function parameters schema; escape embedded quotes and newlines using JSON encoding. Honor parallel_tool_calls=false by returning at most one call. A completed tool returning false is normal data, not a transport error; use the evidence and report missing private references without reading secrets.'),
        ("  fs.writeFileSync(dir+'/schema.json',JSON.stringify(schema),{mode:0o600});", "  fs.writeFileSync(dir+'/schema.json',JSON.stringify(decisionRecovery.constrainSchema(schema,prepared.data)),{mode:0o600});"),
        ("+remoteInstructions+'\\n'+JSON.stringify(prepared.data)", "+remoteInstructions+decisionRecovery.repairInstruction(repair)+'\\n'+JSON.stringify(prepared.data)"),
        ("const value=JSON.parse(fs.readFileSync(dir+'/result.json','utf8'));finish(null,validateDecision(value,prepared.data));", "let raw;try{raw=fs.readFileSync(dir+'/result.json','utf8')}catch{throw decisionRecovery.invalid('result_file')}finish(null,decisionRecovery.parseResult(raw,validateDecision,prepared.data));"),
        ("finish(Error(e.message.startsWith('tool_')||e.message.startsWith('invalid_')?e.message:'invalid_tool_response'));", "finish(decisionRecovery.transportError(e));"),
    ]
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError('recovery_anchor_changed')
        source = source.replace(before, after, 1)
    start = source.index('  // A fast non-zero exit from the engine')
    end = source.index("  console.log(JSON.stringify({event:'internal_tool_decision'", start)
    old = source[start:end]
    if old.count('message=await plan(data)') != 1 or old.count('attempt>=3') != 1:
        raise ValueError('recovery_retry_baseline_changed')
    source = source[:start] + "  const message=await decisionRecovery.chooseDecision(plan,data,event=>console.warn(JSON.stringify(event)));\n" + source[end:]
    return source


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    a.output.write_text(patch(a.source.read_text()))
    a.output.chmod(0o600)

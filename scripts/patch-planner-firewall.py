#!/usr/bin/env python3
"""Pure exact-anchor patch used by deployment; never inspect/log request data."""
def patch(source):
    anchors = (
        ("const {compactPayload}=require('./planner-context');", "const {compactPayload}=require('./planner-context');\nconst {assertToolRequest,scrubText}=require('./secret-firewall.cjs');"),
        ("if(!ajv.validate(tool.parameters,args))throw Error('invalid_tool_arguments');", "if(!ajv.validate(tool.parameters,args))throw Error('invalid_tool_arguments');\n  assertToolRequest(c.name,args);"),
        ("const cleanContent=sanitizeIdentity(value.content);", "const cleanContent=sanitizeIdentity(scrubText(value.content));"),
        ("emit(code==='tool_input_too_large'?413:code==='tool_engine_busy'?429:503,", "emit(code==='tool_input_too_large'?413:code==='tool_secret_reference_required'||code==='invalid_tool_history'?400:code==='tool_engine_busy'?429:503,"),
        ("message:code==='tool_input_too_large'?'Input exceeds safe planner budget; shorten oversized tool arguments.':'Tool decision engine unavailable or returned invalid output.'", "message:code==='tool_input_too_large'?'Input exceeds safe planner budget; shorten oversized tool arguments.':code==='tool_secret_reference_required'?'Use a configured private credential reference; raw secrets and secret-file reads are not allowed.':code==='invalid_tool_history'?'Tool history is incomplete; recover original tool results before continuing.':'Tool decision engine unavailable or returned invalid output.'"),
    )
    if all(after in source for _, after in anchors):
        return source
    if any(source.count(before) != 1 for before, _ in anchors):
        raise ValueError('planner_baseline_changed')
    for before, after in anchors:
        source = source.replace(before, after, 1)
    return source

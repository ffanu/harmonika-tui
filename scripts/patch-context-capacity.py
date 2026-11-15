#!/usr/bin/env python3
"""Prepare a planner candidate; activation is performed separately after review."""
import argparse
import hashlib
import json
from pathlib import Path


def patch(text):
    replacements=[
        ("const {compactPayload}=require('./planner-context');", "const {compactPayload}=require('./planner-context');\nconst contextCapacity=require('./context-capacity.cjs');"),
        (' const prepared=compactPayload(data);', ' const capacity=contextCapacity.read();\n const prepared=compactPayload(data,{maxChars:capacity.transport_soft_chars});'),
        ("  for(const name of ['shell_tool'", "  args.push(...contextCapacity.args(capacity));\n  for(const name of ['shell_tool'")]
    for old,new in replacements:
        if text.count(old)!=1:raise ValueError('context_patch_anchor_changed')
        text=text.replace(old,new,1)
    return text


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    before=a.source.read_bytes();after=patch(before.decode()).encode();a.output.write_bytes(after);a.output.chmod(0o600)
    print(json.dumps({'baseline_sha256':hashlib.sha256(before).hexdigest(),'candidate_sha256':hashlib.sha256(after).hexdigest()}))

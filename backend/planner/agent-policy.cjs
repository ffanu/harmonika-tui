'use strict';
// Production stages the same signed runtime module beside the planner wrapper.
// In the repository use its canonical source; never fall back on a malformed module.
const fs=require('node:fs'),path=require('node:path');
const sibling=path.join(__dirname,'agent-policy-runtime.cjs');
module.exports=require(fs.existsSync(sibling)?sibling:'../../runtime/tools/agent-policy.cjs');

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {upgradeInstructions,REPLACEMENT} = require('../backend/planner/fullstack-workflow.cjs');
const {assertToolRequest} = require('../backend/planner/secret-firewall.cjs');
const tail = 'DEPENDENT TOOL OPERATIONS (all coding/debugging tasks):\nFix dependent operations; fix it before claiming success.\n';
const before = 'REMOTE ENGINEERING WORKFLOW v2026-11-11.9\nRemote scope and credential safeguards.\n'+tail;
test('preserves unrelated scope and upgrades dependent tools with bounded writes',()=>{
 const s=upgradeInstructions(before);
 assert.ok(s.includes('Remote scope and credential safeguards.\n'));
 assert.ok(s.endsWith(REPLACEMENT));
 assert.match(s,/code <=2000 chars/);assert.match(s,/Use env refs for test passwords/);assert.match(s,/bodyless DELETE/);
 assert.match(s,/edit -> tests -> activate -> verify -> report -> read report/);
});
test('idempotent operator upgrade',()=>{const s=upgradeInstructions(before);assert.equal(upgradeInstructions(s),s)});
test('reject unknown baseline or unexpected trailing instructions',()=>{
 for(const s of ['arbitrary',before+'unexpected',before.replace(tail,''),before+tail])
  assert.throws(()=>upgradeInstructions(s));
});
test('respect live 12000 byte reader budget',()=>{
 const huge=before.replace('Remote scope and credential safeguards.', 'x'.repeat(12000));
 assert.throws(()=>upgradeInstructions(huge),/workflow_budget_exceeded/);
});
test('test fixture environment references work without weakening the firewall',()=>{
 assert.doesNotThrow(()=>assertToolRequest('write',{path:'tests/login.test.js',content:'const body={email:"admin@example.test",password:process.env.CRM_EXAM_ADMIN_PASSWORD,};'}));
 assert.doesNotThrow(()=>assertToolRequest('write',{path:'src/Login.tsx',content:'const body={email,password};'}));
 assert.throws(()=>assertToolRequest('write',{path:'tests/login.test.js',content:'const body={password:"synthetic-inline-secret"};'}),/tool_secret_reference_required/);
});

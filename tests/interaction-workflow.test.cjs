const {test}=require('node:test'),assert=require('node:assert/strict');
const {upgradeInstructions,GUIDE,REPLACEMENTS}=require('../backend/planner/interaction-workflow.cjs');
const source='REMOTE ENGINEERING WORKFLOW v2026-11-12.2\nRemote scope and secret firewall stay intact.\n'+REPLACEMENTS.map(([p])=>p+' original bounded guide.').join('\n')+'\nOther operator instructions stay byte-for-byte.\n';
test('changes only selected operator prose and keeps credential scope explicit',()=>{
  const result=upgradeInstructions(source);
  assert.ok(result.includes('Remote scope and secret firewall stay intact.\n'));
  assert.ok(result.includes('Other operator instructions stay byte-for-byte.\n'));
  assert.ok(result.endsWith(GUIDE));assert.match(result,/resetting a password requires explicit change authorization/);
  assert.match(result,/timestamps, MFA state and absent exceptions do not prove/);
  assert.match(result,/After two empty\/unchanged checks/);
  assert.ok(Buffer.byteLength(result)<=12000);
});
test('idempotent hot-read guide and strict marker protection',()=>{
  const result=upgradeInstructions(source);assert.equal(upgradeInstructions(result),result);
  assert.throws(()=>upgradeInstructions(result+'unreviewed trailing data'),/interaction_patch_changed/);
});
test('refuses missing or duplicate workflow anchors and oversized policy',()=>{
  assert.throws(()=>upgradeInstructions('untrusted text'),/invalid_workflow/);
  assert.throws(()=>upgradeInstructions(source.replace(REPLACEMENTS[0][0],'changed prefix')),/interaction_baseline_changed/);
  assert.throws(()=>upgradeInstructions(source+REPLACEMENTS[0][0]+' duplicate'),/interaction_baseline_changed/);
  assert.throws(()=>upgradeInstructions(source+'x'.repeat(12000)),/workflow_budget_exceeded/);
});

'use strict';
const policy=require('./agent-policy.cjs');
const {createHash}=require('node:crypto');
// Exact previously activated footer, accepted only for a bounded operator upgrade.
const priorFooterSha='3cbe8df3b2ebf05a6f36814be6478587e029a7e83eb098a967484886ace7187a';
const previous=require('./interaction-workflow.cjs').GUIDE;
const guide=policy.hotInstructions()+'\n';
function upgradeInstructions(source){
 if(typeof source!=='string'||!source.startsWith('REMOTE ENGINEERING WORKFLOW v'))throw Error('invalid_workflow');
 const marker='AGENT BOUNDARIES v1:';
 if(source.includes(marker)){
  if(source.indexOf(marker)!==source.lastIndexOf(marker)||Buffer.byteLength(source)>12000)throw Error('policy_patch_changed');
  if(source.endsWith(guide))return source;
  const position=source.indexOf(marker),oldFooter=source.slice(position);
  if(createHash('sha256').update(oldFooter).digest('hex')!==priorFooterSha)throw Error('policy_patch_changed');
  const value=source.slice(0,position)+guide;
  if(Buffer.byteLength(value)>12000)throw Error('workflow_budget_exceeded');
  return value;
 }
 if(!source.endsWith(previous)||source.indexOf('LOGIN AND ASYNC EVIDENCE v1:')!==source.lastIndexOf('LOGIN AND ASYNC EVIDENCE v1:'))throw Error('policy_baseline_changed');
 const value=source.slice(0,-previous.length)+guide;
 if(Buffer.byteLength(value)>12000)throw Error('workflow_budget_exceeded');
 return value;
}
module.exports={upgradeInstructions,GUIDE:guide};

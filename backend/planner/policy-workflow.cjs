'use strict';
const policy=require('./agent-policy.cjs');
const previous=require('./interaction-workflow.cjs').GUIDE;
const guide=policy.hotInstructions()+'\n';
function upgradeInstructions(source){
 if(typeof source!=='string'||!source.startsWith('REMOTE ENGINEERING WORKFLOW v'))throw Error('invalid_workflow');
 const marker='AGENT BOUNDARIES v1:';
 if(source.includes(marker)){
  if(source.indexOf(marker)!==source.lastIndexOf(marker)||!source.endsWith(guide)||Buffer.byteLength(source)>12000)throw Error('policy_patch_changed');
  return source;
 }
 if(!source.endsWith(previous)||source.indexOf('LOGIN AND ASYNC EVIDENCE v1:')!==source.lastIndexOf('LOGIN AND ASYNC EVIDENCE v1:'))throw Error('policy_baseline_changed');
 const value=source.slice(0,-previous.length)+guide;
 if(Buffer.byteLength(value)>12000)throw Error('workflow_budget_exceeded');
 return value;
}
module.exports={upgradeInstructions,GUIDE:guide};

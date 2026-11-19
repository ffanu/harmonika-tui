'use strict';
const policy=require('./agent-policy.cjs');
const known=new Set([...Object.values(policy.contract.blockers).flatMap(x=>x.codes),'invalid_tool_response','invalid_tool_arguments','tool_choice_not_satisfied','empty_tool_response','tool_engine_busy','tool_engine_timeout','tool_engine_output_limit','tool_input_too_large','translation_invalid_request','tool_proof_contract_invalid','invalid_tool_history']);
function response(value){
 // Only a mechanical identifier may reach the error envelope or mechanics log.
 const code=typeof value==='string'&&known.has(value)?value:'tool_engine_unavailable';
 const proofInvalid=code==='tool_proof_contract_invalid';
 const status=code==='tool_scope_denied'?403:proofInvalid||code==='translation_invalid_request'?400:code==='tool_input_too_large'?413:code==='tool_secret_reference_required'||code==='invalid_tool_history'?400:code==='tool_engine_busy'?429:503;
 const legacyMessage=proofInvalid?'Operator task proof is invalid or expired; refresh its signed contract before continuing.':code==='tool_input_too_large'?'Conversation exceeds the supported context capacity.':code==='invalid_tool_history'?'Tool history is incomplete; recover original tool results before continuing.':'Tool decision engine unavailable or returned invalid output.';
 const diagnostic=policy.describeBlocker(code);
 const error={code:proofInvalid?'invalid_tool_history':code==='tool_input_too_large'?'context_length_exceeded':code,message:diagnostic?diagnostic.message+' '+diagnostic.next_step:legacyMessage,type:code==='tool_scope_denied'?'permission_error':code==='tool_input_too_large'?'invalid_request_error':'server_error'};
 return {code,status,body:{error,...(diagnostic?{diagnostic}:{})}};
}
module.exports={response};

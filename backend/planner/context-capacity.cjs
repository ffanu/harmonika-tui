'use strict';
// Operator-owned hot-read policy. Caller payloads cannot select model/capacity.
const fs=require('node:fs');
function read(){
 const p=JSON.parse(fs.readFileSync(__dirname+'/context-capacity.json','utf8'));
 if(p.schema_version!==1||p.model!=='gpt-6.1-sol'||p.context_window_tokens!==872000||
    p.auto_compact_tokens!==780000||p.transport_soft_chars!==2800000||p.max_payload_bytes!==8388608)throw Error('context_policy_invalid');
 return p;
}
function args(p=read()){
 return ['-m',p.model,'-c','model_context_window='+p.context_window_tokens,
  '-c','model_auto_compact_token_limit='+p.auto_compact_tokens];
}
module.exports={read,args};

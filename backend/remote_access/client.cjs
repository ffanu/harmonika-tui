'use strict';
/* Internal Node adapter: public model tokens and frontend callers cannot use it. */
const fs=require('node:fs');
function privateToken(file){const info=fs.lstatSync(file);if(!info.isFile()||info.isSymbolicLink()||(info.mode&0o077)||info.uid!==process.getuid())throw Error('unsafe_token_file');return fs.readFileSync(file,'utf8').trim();}
function createClient({baseURL='http://127.0.0.1:18231',tokenFile='/etc/harmonika-remote/api.token'}={}){
 const base=new URL(baseURL);if(base.protocol!=='http:'||!['127.0.0.1','[::1]','localhost'].includes(base.hostname)||base.username||base.password||base.pathname!=='/')throw Error('private_loopback_endpoint_required');
 let session,starting;
 async function request(route,input){const headers={Authorization:'Bearer '+privateToken(tokenFile),'Content-Type':'application/json'};if(session)headers['X-Harmonika-Session']=session;
  const response=await fetch(new URL(route,base),{method:input===undefined?'GET':'POST',headers,body:input===undefined?undefined:JSON.stringify(input),redirect:'error',signal:AbortSignal.timeout(185000)});
  const result=await response.json();return {...result,http_status:response.status};}
 async function invoke(operation,input){if(!session){starting||=request('/api/v1/ssh/sessions',{});const start=await starting;if(!start.ok){starting=undefined;return start;}session=start.session_id;}return request('/api/v1/ssh/'+operation,input);}
 return Object.freeze({targets:()=>request('/api/v1/ssh/targets'),test:input=>invoke('test',input),execute:input=>invoke('execute',input),upload:input=>invoke('upload',input),download:input=>invoke('download',input),tunnel:input=>invoke('tunnel',input)});
}
module.exports={createClient};

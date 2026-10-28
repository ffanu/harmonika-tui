'use strict';
/* Dedicated SSH MCP: native permissions, bounded worker, no local bash/ssh/scp. */
const path=require('node:path'),{spawn}=require('node:child_process');
const {StringDecoder}=require('node:string_decoder');
const paths=require('./paths.cjs');
const {Server}=require(path.join(paths.sdk,'server/index.js'));
const {StdioServerTransport}=require(path.join(paths.sdk,'server/stdio.js'));
const {ListToolsRequestSchema,CallToolRequestSchema}=require(path.join(paths.sdk,'types.js'));
const target={type:'string',minLength:1,maxLength:253,description:'Registered alias or exact registered SSH endpoint user@host:port. Discover with remote_targets first.'};
const props={target,project_root:{type:'string',description:'Absolute selected local project under an operator-approved local root.'},local_path:{type:'string',description:'Relative project file; credentials, symlinks and overwrite are refused.'},remote_path:{type:'string',description:'Relative file below the registered remote root.'}};
const defs=[
 {name:'ssh_connect',skill:'ssh.connect',description:'Verify key-auth SSH connectivity to an operator-registered alias (for example vps). No password, shell or persistent session. Report only verified results.',inputSchema:{type:'object',properties:{target},required:['target'],additionalProperties:false},annotations:{readOnlyHint:true,destructiveHint:false}},
 {name:'ssh_exec',skill:'ssh.exec',description:'Run a registered executable as argv over dedicated SSH. Use this instead of bash ssh. Target and allowed executables are operator policy; never claim success without zero exit. No shell strings or automatic retries.',inputSchema:{type:'object',properties:{target,argv:{type:'array',items:{type:'string',maxLength:8192},minItems:1,maxItems:64},timeout_seconds:{type:'integer',minimum:1,maximum:120}},required:['target','argv'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true}},
 ...['upload','download'].map(action=>({name:'ssh_'+action,skill:'ssh.'+action,description:(action==='upload'?'Upload a selected local project file to':'Download a file from')+' a registered SSH target with SFTP. Bounded files, approved roots, key/host verification, no symlinks or overwrite. No automatic retries on uncertain state.',inputSchema:{type:'object',properties:props,required:['target','project_root','local_path','remote_path'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:action==='upload'}})),
 {name:'remote_targets',skill:'remote.targets',description:'Find operator-configured SSH, Telnet and VNC aliases. Always call before saying an endpoint lacks a registered alias. Optional query is hostname, alias or user@host:port. No credentials are returned.',inputSchema:{type:'object',properties:{query:{type:'string',maxLength:253}},additionalProperties:false},annotations:{readOnlyHint:true,destructiveHint:false}},
 ...['connect','exec'].map(action=>({name:'telnet_'+action,skill:'telnet.'+action,description:action==='connect'?'Authenticate to a registered Telnet service over an encrypted SSH tunnel. Requires configured local credential files and exact login prompt. Never use bash telnet.':'Execute an operator-allowed exact Telnet command after login through SSH tunnel. Completion means prompt received, not POSIX exit status. No automatic retries.',inputSchema:{type:'object',properties:action==='connect'?{target}:{target,command:{type:'string',maxLength:2048}},required:action==='connect'?['target']:['target','command'],additionalProperties:false},annotations:{readOnlyHint:action==='connect',destructiveHint:action!=='connect'}})),
 ...['connect','capture','control'].map(action=>({name:'vnc_'+action,skill:'vnc.'+action,description:action==='connect'?'Authenticate to a registered VNC server over SSH tunnel and verify framebuffer. No external viewer, browser or desktop opened.':action==='capture'?'Capture the current registered VNC screen as private PNG in the selected local project. Existing files are never overwritten. Screenshot content is not logged.':'Send a mouse click, a supported key or printable ASCII text to an operator-authorized VNC target. Native approval required. Never assume an action completed solely because it was sent.',inputSchema:{type:'object',properties:action==='connect'?{target}:action==='capture'?{target,project_root:props.project_root,local_path:props.local_path}:{target,action:{type:'string',enum:['click','key','text']},x:{type:'integer',minimum:0},y:{type:'integer',minimum:0},key:{type:'string'},text:{type:'string',maxLength:512}},required:action==='connect'?['target']:action==='capture'?['target','project_root','local_path']:['target','action'],additionalProperties:false},annotations:{readOnlyHint:action==='connect',destructiveHint:action==='control'}}))
];
// Keep the native interactive tool budget below the gateway's 32-tool ceiling.
// Legacy names remain callable for in-flight history, but new lists use dispatchers.
const compact=[...defs.filter(t=>!t.name.startsWith('telnet_')&&!t.name.startsWith('vnc_')),
 {name:'telnet',description:'Registered Telnet over SSH tunnel. operation connect verifies login; exec runs an operator-allowed exact command. Never use bash telnet or supply credentials.',inputSchema:{type:'object',properties:{target,operation:{type:'string',enum:['connect','exec']},command:{type:'string',maxLength:2048}},required:['target','operation'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true}},
 {name:'vnc',description:'Registered VNC over SSH tunnel. operation connect verifies framebuffer; capture saves private PNG using project_root/local_path; control sends action click(x,y), key(key), or text(text) only when operator permits. No credentials or bash. Verify actions with capture.',inputSchema:{type:'object',properties:{target,operation:{type:'string',enum:['connect','capture','control']},project_root:props.project_root,local_path:props.local_path,action:{type:'string',enum:['click','key','text']},x:{type:'integer',minimum:0},y:{type:'integer',minimum:0},key:{type:'string'},text:{type:'string',maxLength:512}},required:['target','operation'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true}}];
function dispatch(name,args){
 if(name==='telnet'||name==='vnc'){
  const {operation,...input}=args||{};
  if(!(name==='telnet'?['connect','exec']:['connect','capture','control']).includes(operation))return null;
  return {skill:name+'.'+operation,input};
 }
 const def=defs.find(x=>x.name===name);return def?{skill:def.skill,input:args||{}}:null;
}
function invoke(skill,args,signal){return new Promise(resolve=>{
 let child,raw='',finished=false,timer;const decoder=new StringDecoder('utf8');
 const uncertain=['ssh.exec','ssh.upload','telnet.exec','vnc.control'].includes(skill);
 const failure=code=>({ok:false,status:uncertain?'uncertain':'error',error:{code,retry_safe:!uncertain}});
 const done=value=>{if(finished)return;finished=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);resolve(value);};
 const abort=()=>{child?.kill();done(failure('remote_state_unknown'));};
 try{
  child=spawn(path.join(paths.terminal,'ssh-runtime',process.platform==='win32'?'Scripts/python.exe':'bin/python'),['-I',path.join(__dirname,skill.startsWith('ssh.')?'ssh_connector.py':'remote_connector.py'),'invoke'],{stdio:['pipe','pipe','ignore'],windowsHide:true});
  timer=setTimeout(()=>{child.kill();done(failure('ssh_worker_timeout'));},180000);
  child.on('error',()=>done({ok:false,status:'error',error:{code:'ssh_runtime_unavailable',retry_safe:true}}));
  child.stdin.on('error',()=>{});
  child.stdout.on('data',chunk=>{raw+=decoder.write(chunk);if(Buffer.byteLength(raw)>128*1024){child.kill();done(failure('ssh_output_limit'));}});
  child.on('close',()=>{raw+=decoder.end();try{done(JSON.parse(raw));}catch{done(failure('ssh_invalid_response'));}});
  if(signal?.aborted)return abort();signal?.addEventListener('abort',abort,{once:true});
  child.stdin.end(JSON.stringify({skill_id:skill,input:args}));
 }catch{done({ok:false,status:'error',error:{code:'ssh_runtime_unavailable'}})}
});}
async function main(){const server=new Server({name:'harmonika-ssh',version:'1.0.0'},{capabilities:{tools:{}}});
 server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:compact.map(({skill,...def})=>({...def,annotations:{...def.annotations,openWorldHint:true}}))}));
 server.setRequestHandler(CallToolRequestSchema,async(req,extra)=>{const call=dispatch(req.params.name,req.params.arguments);const result=call?await invoke(call.skill,call.input,extra.signal):{ok:false,error:{code:'unknown_tool'}};return {isError:!result.ok,content:[{type:'text',text:JSON.stringify(result)}]};});
 await server.connect(new StdioServerTransport());
}
if(require.main===module)main().catch(()=>{process.stderr.write('Harmonika SSH MCP startup failed.\n');process.exitCode=1});
module.exports={defs,compact,dispatch,invoke};

'use strict';
// Real installed stdio MCP → authenticated media bridge. No model/chat requests.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const runtime=path.join(os.homedir(),'.config/harmonika-terminal/current');
const sdk=require(path.join(runtime,'tools/paths.cjs')).sdk;
const {Client}=require(path.join(sdk,'client/index.js'));
const {StdioClientTransport}=require(path.join(sdk,'client/stdio.js'));
(async()=>{
 const cwd=fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-media-tui-'));fs.writeFileSync(path.join(cwd,'package.json'),'{"name":"isolated-media-tui-smoke"}');
 const transport=new StdioClientTransport({command:process.execPath,args:[path.join(runtime,'tools/mcp-server.cjs')],cwd,env:Object.fromEntries(Object.entries(process.env).filter(([key])=>key!=='HARMONIKA_MEDIA_BRIDGE')),stderr:'pipe'});
 const client=new Client({name:'harmonika-media-smoke',version:'1.0.0'});const start=Date.now();
 try{await client.connect(transport);const capability=await client.callTool({name:'capabilities',arguments:{}});assert.notEqual(capability.isError,true);assert.equal(JSON.parse(capability.content[0].text).backend.capabilities.image_generation,true);const out=await client.callTool({name:'generate_image',arguments:{prompt:'One red triangle on a simple white background, flat icon design, no text',output_file:'sample.png'}},undefined,{timeout:180000});assert.notEqual(out.isError,true);const data=JSON.parse(out.content[0].text);assert.equal(data.status,'ok');assert.equal(data.image_base64,undefined);const bytes=fs.readFileSync(path.join(cwd,'sample.png'));assert.ok(bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])));assert.equal(fs.statSync(path.join(cwd,'sample.png')).mode&0o777,0o600);const report={status:'passed',installed_release:fs.realpathSync(runtime).split('/').pop(),transport:'stdio MCP → HTTPS → private queue → Windows GPU',elapsed_seconds:(Date.now()-start)/1000,image_bytes:bytes.length,provider:data.provider,output_mode:'0600',image_base64_in_tool_context:false,sha256:crypto.createHash('sha256').update(bytes).digest('hex')};fs.writeFileSync('/tmp/harmonika-media-tui-smoke.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));}
 finally{await client.close();fs.rmSync(cwd,{recursive:true,force:true});}
})().catch(e=>{console.error(JSON.stringify({status:'failed',error_type:e.name,code:e.code||'smoke_assertion'}));process.exitCode=1;});

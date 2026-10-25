const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const paths=require('../runtime/tools/paths.cjs');
const {Client}=require(path.join(paths.sdk,'client/index.js'));
const {StdioClientTransport}=require(path.join(paths.sdk,'client/stdio.js'));
test('fresh HOME can initialize portable MCP with provisioned dependencies and never inherits owner SSH',async()=>{
 const home=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-public-home-')));
 const env={...process.env,HOME:home,HARMONIKA_TOOLS_HOME:paths.tools,HARMONIKA_MEDIA_BRIDGE:'0'};
 for(const key of Object.keys(env))if(key==='HARMONIKA_TRANSPORT'||key.startsWith('HARMONIKA_OWNER_SSH_'))delete env[key];
 const transport=new StdioClientTransport({command:process.execPath,args:[path.resolve(__dirname,'../runtime/tools/mcp-server.cjs')],env,stderr:'pipe'});
 const client=new Client({name:'portable-test',version:'1.0.0'});
 try {
  await client.connect(transport);
  assert.equal((await client.listTools()).tools.length,8);
  const directory=path.join(home,'Projects','Example');
  let result=await client.callTool({name:'create_directory',arguments:{directory}});
  assert.equal(result.isError,false);assert.ok(fs.statSync(directory).isDirectory());
  result=await client.callTool({name:'capabilities',arguments:{}});
  const state=JSON.parse(result.content[0].text);
  assert.equal(result.isError,true);assert.equal(state.backend.available,false);
  result=await client.callTool({name:'generate_image',arguments:{prompt:'test',output_file:path.join(directory,'test.png')}});
  assert.equal(result.isError,true);assert.equal(JSON.parse(result.content[0].text).error,'licensed_media_transport_unavailable');
  assert.equal(fs.existsSync(path.join(directory,'test.png')),false);
 }finally {await client.close();fs.rmSync(home,{recursive:true,force:true});}
});

test('default MCP selects licensed HTTPS media without an environment flag',async()=>{
 const home=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'harmonika-default-media-')));
 const env={...process.env,HOME:home,HARMONIKA_TOOLS_HOME:paths.tools};
 for(const key of Object.keys(env))if(key==='HARMONIKA_MEDIA_BRIDGE'||key==='HARMONIKA_TRANSPORT'||key.startsWith('HARMONIKA_OWNER_SSH_'))delete env[key];
 const transport=new StdioClientTransport({command:process.execPath,args:[path.resolve(__dirname,'../runtime/tools/mcp-server.cjs')],env,cwd:home,stderr:'pipe'});
 const client=new Client({name:'default-media-test',version:'1.0.0'});
 try{
  await client.connect(transport);
  const directory=path.join(home,'Projects','Example');
  await client.callTool({name:'create_directory',arguments:{directory}});
  const result=await client.callTool({name:'generate_image',arguments:{prompt:'test',output_file:path.join(directory,'test.png')}});
  assert.equal(result.isError,true);
  assert.equal(JSON.parse(result.content[0].text).error,'media_key_unavailable');
  assert.equal(fs.existsSync(path.join(directory,'test.png')),false);
 }finally{await client.close();fs.rmSync(home,{recursive:true,force:true});}
});

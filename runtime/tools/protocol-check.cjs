'use strict';
const path=require('node:path'),os=require('node:os');
const tools=process.env.HARMONIKA_TOOLS_HOME||path.join(process.env.HARMONIKA_CONFIG_DIR||path.join(os.homedir(),'.config/harmonika'),'harmonika-tools');
const sdk=path.join(tools,'node_modules/@modelcontextprotocol/sdk/dist/cjs');
const {Client}=require(path.join(sdk,'client/index.js'));
const {StdioClientTransport}=require(path.join(sdk,'client/stdio.js'));
(async()=>{
 const transport=new StdioClientTransport({command:process.execPath,args:[process.argv[2]],stderr:'pipe'});
 const client=new Client({name:'harmonika-preflight',version:'1.0.0'});
 const timer=setTimeout(()=>{transport.close();process.exitCode=1;},15000);timer.unref();
 try {
  await client.connect(transport);
  const list=await client.listTools(),names=list.tools.map(x=>x.name);
  for(const name of ['create_directory','capabilities','web_search','analyze_image','read_pdf','analyze_video','transcribe_audio','generate_image'])if(!names.includes(name))throw Error('missing_tool');
  process.stdout.write(JSON.stringify({ok:true,tools:names}));
 }finally{clearTimeout(timer);await client.close();}
})().catch(()=>{process.stderr.write('MCP protocol check failed');process.exitCode=1;});

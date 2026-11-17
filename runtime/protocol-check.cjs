'use strict';
const path=require('node:path'),os=require('node:os'),fs=require('node:fs');
const tools=process.env.HARMONIKA_TOOLS_HOME||path.join(process.env.HARMONIKA_CONFIG_DIR||path.join(os.homedir(),'.config/harmonika'),'harmonika-tools');
// Vendored deps shipped in the package win; shared dir is the fallback.
const vendored=path.join(__dirname,'vendor/sdk');
const shared=path.join(tools,'node_modules/@modelcontextprotocol/sdk/dist/cjs');
const sdk=fs.existsSync(vendored)?vendored:shared;
if(!fs.existsSync(path.join(sdk,'client/index.js'))){
 process.stderr.write('mcp_sdk_missing: paket tidak lengkap atau dependensi legacy belum terpasang. Jalankan ulang installer terbaru.\n');
 process.exit(1);
}
const {Client}=require(path.join(sdk,'client/index.js'));
const {StdioClientTransport}=require(path.join(sdk,'client/stdio.js'));
(async()=>{
 const pathVars=['HARMONIKA_CONFIG_DIR','HARMONIKA_GLOBAL_CONFIG','HARMONIKA_GLOBAL_DATA','HARMONIKA_TOOLS_HOME','HARMONIKA_TERMINAL_HOME','HARMONIKA_DOWNLOADS_DIR'];
 const env=Object.fromEntries(pathVars.filter(n=>process.env[n]!==undefined).map(n=>[n,process.env[n]]));
 const transport=new StdioClientTransport({command:process.execPath,args:[process.argv[2]],env,stderr:'pipe'});
 const client=new Client({name:'harmonika-preflight',version:'1.0.0'});
 const timer=setTimeout(()=>{transport.close();process.exitCode=1;},15000);timer.unref();
 try {
  await client.connect(transport);
  const list=await client.listTools(),names=list.tools.map(x=>x.name);
  for(const name of ['create_directory','capabilities','web_search','analyze_image','read_pdf','analyze_video','transcribe_audio','generate_image'])if(!names.includes(name))throw Error('missing_tool');
  process.stdout.write(JSON.stringify({ok:true,tools:names}));
 }finally{clearTimeout(timer);await client.close();}
})().catch(()=>{process.stderr.write('MCP protocol check failed');process.exitCode=1;});

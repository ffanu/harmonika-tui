'use strict';
const path=require('node:path'),os=require('node:os'),fs=require('node:fs');
const home=os.homedir();
const config=process.env.HARMONIKA_CONFIG_DIR||path.join(home,'.config/harmonika');
const tools=process.env.HARMONIKA_TOOLS_HOME||path.join(config,'harmonika-tools');
const terminal=process.env.HARMONIKA_TERMINAL_HOME||path.join(home,'.config/harmonika-terminal');
const downloads=process.env.HARMONIKA_DOWNLOADS_DIR||path.join(home,'Downloads','harmonika-ai');
const downloadFolders=Object.freeze({
  root:downloads,
  gambar:path.join(downloads,'gambar'),
  document:path.join(downloads,'document'),
  zip:path.join(downloads,'zip'),
});
// Vendored-first: dependencies shipped inside the package work without npm
// on any OS; the shared harmonika-tools dir remains the fallback/upgrade path.
const vendoredSdk=path.join(__dirname,'vendor/sdk');
const sharedSdk=path.join(tools,'node_modules/@modelcontextprotocol/sdk/dist/cjs');
module.exports={home,config,tools,terminal,downloads,downloadFolders,sdk:fs.existsSync(vendoredSdk)?vendoredSdk:sharedSdk};

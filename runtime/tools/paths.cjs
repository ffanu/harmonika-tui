'use strict';
const path=require('node:path'),os=require('node:os');
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
module.exports={home,config,tools,terminal,downloads,downloadFolders,sdk:path.join(tools,'node_modules/@modelcontextprotocol/sdk/dist/cjs')};

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const projectAccess = require('./project-access.cjs');
const {terminal} = require('./paths.cjs');
const FILE_TOOLS = new Set(['view','ls','glob','grep','write','edit','multiedit']);
const LSP_TOOLS = new Set(['lsp_diagnostics','lsp_references','lsp_symbols','lsp_definition','lsp_implementation','lsp_call_hierarchy']);
const MARKERS = ['.git','package.json','go.mod','composer.json','Cargo.toml','pyproject.toml','settings.gradle','settings.gradle.kts','build.gradle','build.gradle.kts','AGENTS.md'];
function inside(root, value) { const relative=path.relative(root,value); return relative==='' || (!relative.startsWith('..'+path.sep) && relative!=='..' && !path.isAbsolute(relative)); }
function canonical(value) {
  let current=path.resolve(value); const missing=[];
  while (!fs.existsSync(current)) {
    // A dangling symlink is not a new safe file.
    try { if(fs.lstatSync(current).isSymbolicLink()) throw Error('dangling_symlink'); } catch(error) { if(error.code!=='ENOENT') throw error; }
    const parent=path.dirname(current); if(parent===current) throw Error('invalid_path');
    missing.unshift(path.basename(current)); current=parent;
  }
  return path.join(fs.realpathSync(current),...missing);
}
function privatePath(value) {
  if(inside(path.join(os.homedir(),'Library'),value)) return true;
  const parts=value.split(path.sep).map(p=>p.toLowerCase());
  const sourceFolders=new Set(['.github','.vscode','.cursor','.agents']);
  if(parts.some((p,index)=>p.startsWith('.')&&!sourceFolders.has(p)&&(index<parts.length-1||(fs.existsSync(value)&&fs.statSync(value).isDirectory()))))return true;
  if(parts.some(p=>['.ssh','.config','.codex','.claude','.harmonika','.aws','.gnupg','.git','secrets','credentials','node_modules','.venv','venv'].includes(p))) return true;
  const name=parts.at(-1)||'';
  return (name==='.env' || (name.startsWith('.env.')&&!['.env.example','.env.sample','.env.template'].includes(name))) || /\.(pem|p12|pfx|key|keystore|tfstate)(\.backup)?$/.test(name) || /^(credentials(?:\.|$)|service[-_]account.*\.json$|.*firebase-adminsdk.*\.json$|google-credentials.*\.json$)/.test(name) || ['harmonika-api-key','id_rsa','id_ed25519','.npmrc','.netrc','.pypirc'].includes(name);
}
function managedSkillRead(value) {
  if(typeof value!=='string'||!path.isAbsolute(value))return false;
  try {
    const base=fs.realpathSync(path.join(terminal,'current/skills'));
    const file=canonical(value),relative=path.relative(base,file);
    const engineeringResource=/^harmonika-engineering\/scripts\/engineering_analyzers\.py$/.test(relative);
    const seoResource=/^harmonika-seo\/scripts\/seo_analyzers\.py$/.test(relative);
    const documentResource=/^harmonika-documents\/(?:manifest\.yaml|requirements\.txt|scripts\/(?:document_common|pdf_worker|pdf_handler|docx_handler|xlsx_handler|pptx_handler)\.py)$/.test(relative);
    return inside(base,file)&&(documentResource||seoResource||engineeringResource||/^harmonika-(?:ui|debug|browser-qa|documents|seo|engineering|git|release|tui-dev|async-concurrency|ai-integration|terminal-qa|state-persistence)\/(?:SKILL\.md|references\/[A-Za-z0-9][A-Za-z0-9_-]*\.md|scripts\/(?:smoke\.cjs|documents\.py|terminal_qa\.py|fixture\.py|async_runtime\.py|stream_protocol\.py))$/.test(relative))&&fs.statSync(file).isFile();
  }catch{return false;}
}
function evaluate(payload) {
  const deny=reason=>({version:1,decision:'deny',reason});
  if(!payload || (!FILE_TOOLS.has(payload.tool_name)&&!LSP_TOOLS.has(payload.tool_name)&&payload.tool_name!=='bash')) return {};
  try {
    if(typeof payload.cwd!=='string' || !path.isAbsolute(payload.cwd)) return deny('Folder proyek tidak valid.');
    const declaredRoot=path.resolve(payload.cwd), home=fs.realpathSync(os.homedir());
    let root=canonical(payload.cwd);
    const input=payload.tool_input;
    if(!input || typeof input!=='object' || Array.isArray(input)) return deny('Parameter tool file tidak valid.');
    // Skills are trusted public instructions, not private account data. Reads only;
    // retain native approvals and never permit edits to installed skill files.
    if(payload.tool_name==='view'&&managedSkillRead(input.file_path))return {};
    const broad=[path.parse(root).root,home,path.dirname(home),path.join(home,'Projects'),...['Desktop','Documents','Downloads','Pictures','Movies','Music'].map(n=>path.join(home,n))];
    if(broad.includes(root)) {
      const hint=payload.tool_name==='bash'?input.working_dir:(input.file_path??input.path);
      const selected=projectAccess.findRoot(hint);
      if(!selected) return deny('Buat/pilih folder proyek dengan MCP harmonika create_directory terlebih dahulu, lalu gunakan path absolut folder tersebut. Home tidak boleh dipindai.');
      root=canonical(selected);
    }
    if(privatePath(root)) return deny('Folder privat bukan folder proyek.');
    const projects=path.join(home,'Projects');
    if(!(inside(projects,root)&&root!==projects)&&projectAccess.findRoot(root)!==root&&!MARKERS.some(m=>fs.existsSync(path.join(root,m)))) return deny('Folder ini belum dikenali sebagai proyek. Gunakan MCP create_directory untuk memilih folder tujuan.');
    const name=payload.tool_name;
    if(name==='bash') {
      const working=input.working_dir??root;
      if(typeof working!=='string'||!inside(root,canonical(path.resolve(declaredRoot,working)))) return deny('Terminal harus tetap di folder proyek.');
      const command=input.command;
      if(typeof command!=='string'||!command.trim()||command.length>8000) return deny('Perintah terminal tidak valid.');
      if(/\b(?:kill|pkill|killall|shutdown|reboot|poweroff|halt|sudo|su|launchctl|systemctl|osascript|ssh|scp|sftp|curl|wget|rm|rmdir|chmod|chown)\b/i.test(command)||/git\s+(?:reset\b[^\n]*--hard|clean\b|push\b[^\n]*(?:--force|-f)|checkout\s+--)/i.test(command)||/\.ssh|\.codex|\.config|\.env\b|\bid_(?:rsa|ed25519)\b|os\.kill|process\.kill|\$\(|`/.test(command)||/\bcd\s+(?:["']?(?:\/|~|\.\.))/.test(command)) return deny('Perintah berisiko atau akses di luar proyek diblokir. Gunakan tool yang sesuai dan persetujuan pengguna; jangan hentikan proses pengguna.');
      return {};
    }
    if(LSP_TOOLS.has(name)&&!input.file_path&&!input.path) return {};
    const supplied=['view','write','edit','multiedit'].includes(name)?input.file_path:(input.file_path??input.path??'.');
    if(typeof supplied!=='string'||!supplied||supplied.includes('\0')||supplied.startsWith('~')) return deny('Gunakan path file yang valid di dalam proyek.');
    if(name==='view'&&supplied.startsWith('harmonika:')) return {};
    const lexical=path.resolve(declaredRoot,supplied), resolved=canonical(lexical);
    if(!inside(root,resolved)||privatePath(lexical)||privatePath(resolved)) return deny('Akses di luar folder proyek, data privat, dependensi, atau berkas kredensial ditolak.');
    if(name==='glob'&&typeof input.pattern==='string'&&(path.isAbsolute(input.pattern)||input.pattern.split(/[\\/]/).includes('..'))) return deny('Pola pencarian tidak boleh keluar dari proyek.');
    // No allow decision: keep native Harmonika permission checks and edit previews.
    return {};
  } catch (_) { return deny('Path tidak dapat diverifikasi dengan aman. Pilih file di dalam proyek.'); }
}
if(require.main===module) {
  let body='';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data',chunk=>{body+=chunk;if(body.length>262144){process.stderr.write('Parameter tool terlalu besar.');process.exit(2);}});
  process.stdin.on('end',()=>{
    try {process.stdout.write(JSON.stringify(evaluate(JSON.parse(body))));}
    catch (_) {process.stderr.write('Parameter hook tidak valid.');process.exitCode=2;}
  });
}
module.exports={evaluate};

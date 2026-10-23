'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const os=require('node:os');
const {evaluate}=require('../runtime/tools/project-file-guard.cjs');
function fixture(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'crush-guard-test-'));fs.writeFileSync(path.join(dir,'package.json'),'{}');t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;}
function call(cwd,name,input){return evaluate({cwd,tool_name:name,tool_input:input});}
test('file reads/edits inside a project keep native permission checks',t=>{const root=fixture(t);for(const name of ['view','write','edit','multiedit'])assert.deepEqual(call(root,name,{file_path:'src/app.js'}),{});});
test('listing and searching a project is available',t=>{const root=fixture(t);for(const name of ['ls','grep','glob'])assert.deepEqual(call(root,name,{pattern:'*.js'}),{});});
test('Home and broad personal folders are blocked',()=>{for(const root of [os.homedir(),path.join(os.homedir(),'Projects'),path.join(os.homedir(),'Downloads'),'/'])assert.equal(call(root,'ls',{}).decision,'deny');});
test('paths outside the project are blocked',t=>{const root=fixture(t);for(const file of ['../outside.js','/etc/hosts',path.join(os.homedir(),'.ssh/id_ed25519')])assert.equal(call(root,'view',{file_path:file}).decision,'deny');});
test('symlink escape for existing files and newly created descendants is blocked',t=>{const root=fixture(t);fs.symlinkSync(os.homedir(),path.join(root,'escape'));assert.equal(call(root,'view',{file_path:'escape/.config/crush/harmonika-api-key'}).decision,'deny');assert.equal(call(root,'write',{file_path:'escape/new-file.js'}).decision,'deny');});
test('legitimate project-root aliases such as macOS /tmp to /private/tmp are accepted',t=>{const root=fixture(t);const alias=root+'-alias';fs.symlinkSync(root,alias);t.after(()=>fs.unlinkSync(alias));assert.deepEqual(call(alias,'write',{file_path:path.join(alias,'hello.txt')}),{});assert.deepEqual(call(alias,'view',{file_path:path.join(root,'package.json')}),{});});
test('dangling symlinks are rejected',t=>{const root=fixture(t);fs.symlinkSync(path.join(root,'missing'),path.join(root,'dangling'));assert.equal(call(root,'write',{file_path:'dangling'}).decision,'deny');});
test('credentials and dependency internals are blocked',t=>{const root=fixture(t);for(const file of ['.env','.env.production','secrets/api.json','.git/config','.ssh/key','node_modules/pkg/a.js','.venv/secret','server.key'])assert.equal(call(root,'view',{file_path:file}).decision,'deny');assert.deepEqual(call(root,'view',{file_path:'.env.example'}),{});});
test('glob traversal patterns are denied',t=>{const root=fixture(t);for(const pattern of ['../*.js','/Users/id/**'])assert.equal(call(root,'glob',{pattern}).decision,'deny');});
test('unmarked random directories are not treated as projects',t=>{const root=fixture(t);fs.unlinkSync(path.join(root,'package.json'));assert.equal(call(root,'ls',{}).decision,'deny');});
test('invalid parameters fail closed and non-file UI tools stay available',t=>{const root=fixture(t);assert.equal(call(root,'view',{}).decision,'deny');assert.equal(call(root,'write',{file_path:'~/.ssh/x'}).decision,'deny');assert.deepEqual(call(os.homedir(),'question',{}),{});assert.deepEqual(call(os.homedir(),'todos',{}),{});});
test('ordinary library source code remains readable while credential files are denied',t=>{const root=fixture(t);assert.deepEqual(call(root,'view',{file_path:'src/library/util.js'}),{});for(const file of ['credentials.json','service-account-prod.json','service_account.json','.npmrc','.netrc','infra/state.tfstate','infra/state.tfstate.backup'])assert.equal(call(root,'view',{file_path:file}).decision,'deny');});
test('malformed hook JSON returns Crush documented deny exit code 2, not a generic error',()=>{const {spawnSync}=require('node:child_process');const result=spawnSync(process.execPath,[path.join(__dirname,'../runtime/tools/project-file-guard.cjs')],{input:'{bad',encoding:'utf8'});assert.equal(result.status,2);assert.equal(result.stdout,'');assert.match(result.stderr,/tidak valid/);});
test('project build/test commands still require native approval',t=>{const root=fixture(t);for(const command of ['npm test','npm run build','node --check src/app.js','git diff --stat'])assert.deepEqual(call(root,'bash',{command}),{});});
test('terminal dangerous commands and private/remote access are denied',t=>{const root=fixture(t);for(const command of ['pkill codex','killall opencode','rm -rf .','ssh root@server','cat .env','git reset --hard','echo $(whoami)','cd /Users/id && ls'])assert.equal(call(root,'bash',{command}).decision,'deny');assert.equal(call(root,'bash',{command:'pwd',working_dir:os.homedir()}).decision,'deny');});
test('LSP operations stay in project, including workspace diagnostics',t=>{const root=fixture(t);assert.deepEqual(call(root,'lsp_diagnostics',{}),{});assert.deepEqual(call(root,'lsp_symbols',{file_path:'src/app.ts'}),{});assert.equal(call(root,'lsp_definition',{path:os.homedir(),symbol:'x'}).decision,'deny');});
test('installed public skill instructions readable but not editable and credentials stay blocked',()=>{
  const home=os.homedir(),skill=path.join(home,'.config/harmonika-crush/current/skills/harmonika-documents/SKILL.md');
  assert.deepEqual(call(home,'view',{file_path:skill}),{});
  assert.equal(call(home,'write',{file_path:skill}).decision,'deny');
  assert.equal(call(home,'view',{file_path:path.join(home,'.config/crush/harmonika-api-key')}).decision,'deny');
});
test('Home session can use an explicitly selected project without opening Home or other folders',t=>{
  const root=fs.realpathSync(fixture(t));fs.unlinkSync(path.join(root,'package.json'));
  const access=require('../runtime/tools/project-access.cjs'),original=access.findRoot;
  access.findRoot=target=>typeof target==='string'&&(target===root||target.startsWith(root+path.sep))?root:null;
  t.after(()=>{access.findRoot=original;});
  const home=os.homedir();
  assert.deepEqual(call(home,'ls',{path:root}),{});
  assert.deepEqual(call(home,'write',{file_path:path.join(root,'index.php')}),{});
  assert.deepEqual(call(home,'bash',{working_dir:root,command:'mkdir -p assets'}),{});
  assert.equal(call(home,'ls',{path:home}).decision,'deny');
  assert.equal(call(home,'write',{file_path:'index.php'}).decision,'deny');
  assert.equal(call(home,'bash',{command:'mkdir -p unknown'}).decision,'deny');
  fs.symlinkSync(home,path.join(root,'escape'));
  assert.equal(call(home,'write',{file_path:path.join(root,'escape','outside.php')}).decision,'deny');
});

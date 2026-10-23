'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {createAccess}=require('../runtime/tools/project-access.cjs');
function setup(t){const home=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'crush-directory-')));t.after(()=>fs.rmSync(home,{recursive:true,force:true}));return {home,access:createAccess({home})};}
test('creates nested folder with spaces and remembers scoped root',t=>{
  const {home,access}=setup(t),directory=path.join(home,'Downloads','uji coba','src');
  const result=access.createDirectory(directory);
  assert.equal(result.created,true);assert.ok(fs.statSync(directory).isDirectory());
  assert.equal(access.findRoot(path.join(directory,'index.php')),directory);
  assert.equal(access.findRoot(path.join(home,'Downloads','other','index.php')),null);
  assert.equal(access.createDirectory(directory).created,false);
});
test('rejects broad roots, private folders, traversal and arbitrary absolute targets',t=>{
  const {home,access}=setup(t);
  for(const d of [home,path.join(home,'Downloads'),path.join(home,'.ssh','project'),'/etc/evil',path.join(home,'Downloads','../outside'),path.join(home,'Downloads','.env')])assert.throws(()=>access.createDirectory(d));
  assert.throws(()=>access.createDirectory('relative/project'));
});
test('rejects symlink and existing regular file without overwriting',t=>{
  const {home,access}=setup(t),base=path.join(home,'Projects');fs.mkdirSync(base);
  const link=path.join(base,'escape');fs.symlinkSync(home,link);
  assert.throws(()=>access.createDirectory(path.join(link,'project')));
  const file=path.join(base,'existing');fs.writeFileSync(file,'KEEP');
  assert.throws(()=>access.createDirectory(file));assert.equal(fs.readFileSync(file,'utf8'),'KEEP');
});
test('does not authorize symlink replacement of a selected project',t=>{
  const {home,access}=setup(t),directory=path.join(home,'Projects','selected');
  access.createDirectory(directory);fs.rmdirSync(directory);fs.symlinkSync(home,directory);
  assert.equal(access.findRoot(path.join(directory,'sensitive')),null);
});

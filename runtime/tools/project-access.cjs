'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {createHash} = require('node:crypto');

function createAccess({home=os.homedir(), stateDir=path.join(home,'.config/harmonika/project-access')}={}) {
  const inside=(root,target)=>{const r=path.relative(root,target);return r===''||(!r.startsWith('..'+path.sep)&&r!=='..'&&!path.isAbsolute(r));};
  const containers=['Downloads','Projects'].map(n=>path.join(home,n));
  function allowed(target) {
    return containers.some(base=>target!==base&&inside(base,target)) &&
      !path.relative(home,target).split(path.sep).some(p=>p.startsWith('.')||['secrets','credentials','node_modules','venv'].includes(p.toLowerCase()));
  }
  function findRoot(target) {
    if(typeof target!=='string'||!path.isAbsolute(target))return null;
    let names;try{names=fs.readdirSync(stateDir).filter(n=>/^[a-f0-9]{64}\.json$/.test(n));}catch{return null;}
    const matches=[];
    for(const name of names.slice(0,512)) {
      try {
        const file=path.join(stateDir,name), stat=fs.lstatSync(file);
        if(!stat.isFile()||stat.size>4096)continue;
        const record=JSON.parse(fs.readFileSync(file,'utf8'));
        if(!allowed(record.root)||fs.realpathSync(record.root)!==record.root||!fs.statSync(record.root).isDirectory())continue;
        if(inside(record.root,path.resolve(target)))matches.push(record.root);
      }catch{}
    }
    return matches.sort((a,b)=>b.length-a.length)[0]||null;
  }
  function createDirectory(directory) {
    if(typeof directory!=='string'||!path.isAbsolute(directory)||directory.includes('\0')||directory.length>4096)throw Error('absolute_directory_required');
    const destination=path.resolve(directory);
    if(!allowed(destination))throw Error('directory_must_be_inside_downloads_or_projects');
    const base=containers.find(p=>inside(p,destination));
    fs.mkdirSync(base,{recursive:true});
    if(fs.realpathSync(base)!==base)throw Error('symlink_directory_not_allowed');
    const parts=path.relative(base,destination).split(path.sep);
    if(parts.length>6||parts.some(p=>!p||p.length>100))throw Error('invalid_directory_name');
    let cursor=base;
    for(const part of parts) {
      cursor=path.join(cursor,part);
      try {const stat=fs.lstatSync(cursor);if(stat.isSymbolicLink()||!stat.isDirectory())throw Error('unsafe_directory');}
      catch(e){if(e.code!=='ENOENT')throw e;}
    }
    const created=!fs.existsSync(destination);
    fs.mkdirSync(destination,{recursive:true,mode:0o755});
    const root=fs.realpathSync(destination);
    if(root!==destination)throw Error('symlink_directory_not_allowed');
    fs.mkdirSync(stateDir,{recursive:true,mode:0o700});
    const file=path.join(stateDir,createHash('sha256').update(root).digest('hex')+'.json');
    // Exclusive record creation avoids concurrent writers losing other projects.
    try {fs.writeFileSync(file,JSON.stringify({root}),{flag:'wx',mode:0o600});}
    catch(e){if(e.code!=='EEXIST')throw e;}
    return {ok:true,status:'ok',directory:root,created,
      usage:'Gunakan path absolut di directory ini untuk tool file; bash memakai working_dir directory ini. Tidak perlu membuat folder secara manual.'};
  }
  return {findRoot,createDirectory};
}
module.exports={createAccess,...createAccess()};

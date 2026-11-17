'use strict';
// Lexical client-reported scope only. No filesystem, executor, logs or authority.
const path=require('node:path'),{createHash}=require('node:crypto');
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const fields=['owner','owner_id','project','project_id','workspace_id','target','target_id','attempt','attempt_id','revision','source_revision','ref','commit_sha'];
const revisionFields=new Set(['revision','source_revision']);
function normalized(value,base=null){
 if(typeof value!=='string'||!value.trim()||value.length>4096||/[\x00\r\n$`]/.test(value))return null;
 const v=value.trim();
 if(/^[A-Za-z]:[\\/]|^\\\\/.test(v)){
  let clean=path.win32.normalize(v);if(clean.length>path.win32.parse(clean).root.length)clean=clean.replace(/[\\/]$/,'');
  return 'win:'+clean.replace(/\\/g,'/').replace(/^[A-Z]:/,s=>s.toLowerCase());
 }
 if(v.startsWith('/')){const clean=path.posix.normalize(v);return 'posix:'+(clean.length>1?clean.replace(/\/$/,''):clean);}
 if(!base)return null;
 return normalized((base.startsWith('win:')?path.win32:path.posix).join(base.slice(base.indexOf(':')+1),v));
}
function parent(value){
 const win=value.startsWith('win:'),prefix=win?'win:':'posix:',p=value.slice(prefix.length);
 const folder=(win?path.win32:path.posix).dirname(p);
 return normalized(win?folder.replace(/\//g,'\\'):folder);
}
function ancestors(value){
 const result=[];let current=value;
 for(let i=0;current&&i<256;i++){
  result.push(hash(current));const next=parent(current);if(!next||next===current)break;current=next;
 }
 return result;
}
function sourcesIdentity(sources){
 const ids={};let invalid=false;
 for(const source of sources.filter(s=>s&&typeof s==='object'&&!Array.isArray(s)))for(const f of fields)if(Object.hasOwn(source,f)){
  const value=source[f];if(typeof value!=='string'||!value||value.length>512){invalid=true;continue;}
  const key=f==='source_revision'?'revision':f;
  (ids[key]||=[]).push(hash(value));
 }
 for(const f of Object.keys(ids)){ids[f]=[...new Set(ids[f])];if(ids[f].length>1)invalid=true;}
 return {ids,invalid};
}
function identityConflict(a,b){
 return fields.some(f=>a?.ids?.[f]?.length&&b?.ids?.[f]?.length&&!a.ids[f].every(v=>b.ids[f].includes(v)));
}
function inspect(name,args,body,{write=false,read=false,test=false,status=false,operation=false}={}){
 const wrappedRead=read&&body&&typeof body==='object'&&(typeof body.content==='string'||typeof body.output==='string');
 const returned=write||test||status||operation?[body,body?.metadata]:wrappedRead?[body.metadata]:[];
 // Test target is an entry-point path, not an executor target identity.
 const requestedArgs={...args};if(test)delete requestedArgs.target;
 const requested=sourcesIdentity([requestedArgs]),out=sourcesIdentity(returned);
 // A write may return a new revision after consuming an expected old revision.
 const previous={...requested,ids:{...requested.ids}};
 if(write||operation)for(const f of revisionFields)delete previous.ids[f];
 const conflict=identityConflict(previous,out);
 const ids={...previous.ids,...out.ids};
 const rootSources=[args,...returned].filter(s=>s&&typeof s==='object');
 const roots=[];let explicit=false,ambiguous=false;
 for(const source of rootSources)for(const field of ['cwd','workdir','working_directory'])if(Object.hasOwn(source,field)){
  explicit=true;const root=normalized(source[field]);if(!root)ambiguous=true;else roots.push(root);
 }
 let distinct=[...new Set(roots)];if(distinct.length>1)ambiguous=true;
 if(name==='run_commands'&&Array.isArray(args?.commands)&&args.commands.length===1){
  const command=args.commands[0];
  if(typeof command==='string'&&/(?:^|\s)(?:--prefix|--cwd|--directory|--workspace|--workspaces|--rootdir|-C)(?:\s|=|$)/.test(command))ambiguous=true;
  const match=typeof command==='string'?command.match(/^\s*cd\s+(?:\/d\s+)?(?:"([^"\n]+)"|'([^'\n]+)'|([^\s;&|<>`]+))\s*&&\s*([\s\S]*)$/):null;
  if(match){
   explicit=true;const root=normalized(match[1]||match[2]||match[3],distinct[0]);
   if(!root||/(?:&&|;|\|)\s*cd\s/i.test(match[4]))ambiguous=true;
   distinct=root?[root]:[];
  }
 }
 if(distinct.some(root=>parent(root)===root))ambiguous=true;
 const inputPaths=typeof args?.path==='string'?[args.path]:Array.isArray(args?.files)?args.files.map(p=>typeof p==='string'?p:p?.path):[];
 const resolved=(write||read?inputPaths:[]).map(p=>normalized(p,distinct[0])).filter(Boolean);
 const revisionKinds=[...new Set(returned.filter(Boolean).map(s=>s.revision_scope).filter(v=>v!==undefined))];
 const badRevisionKind=revisionKinds.length>1||revisionKinds.some(v=>!['workspace','file'].includes(v));
 return {ids,conflict,invalid:requested.invalid||out.invalid,ambiguous,
  revision_scope:badRevisionKind?'unknown':revisionKinds[0]||'unknown',
  read_receipt:!!read,
  pinned_ref:!!(ids.ref?.length||ids.commit_sha?.length||requested.ids.revision?.length),
  content_hash:wrappedRead?hash(body.content??body.output):null,
  expected_content_hash:write&&['fs.write','code.write','write_file','write_source'].includes(name)&&typeof args?.content==='string'?hash(args.content):null,
  roots:distinct.map(hash),explicit_root:explicit,
  files:resolved.map(hash),file_ancestors:resolved.map(ancestors)};
}
function compatible(a,b){return !a?.invalid&&!b?.invalid&&!a?.conflict&&!b?.conflict&&!identityConflict(a,b);}
function combine(a,b){return !a?b:!b?a:{...a,...b,ids:{...a.ids,...b.ids}};}
function covers(check,write){
 if(check?.isolated_check)return false; // Engine fixture checks never verify a user project's edits.
 if(!compatible(check,write)||check?.ambiguous||write?.ambiguous)return false;
 if(check?.pinned_ref&&['ref','commit_sha','revision'].some(f=>check.ids[f]?.length&&!write?.ids?.[f]?.length))return false;
 if(write?.expected_content_hash&&check?.read_receipt&&write.expected_content_hash!==check.content_hash)return false;
 if(check?.roots?.length&&write?.file_ancestors?.length&&!write.file_ancestors.every(parents=>check.roots.some(root=>parents.includes(root))))return false;
 return true;
}
function writeKey(scope,fallback,fileIndex=0){
 return hash([scope?.files?.[fileIndex]||fallback,Object.fromEntries(Object.entries(scope?.ids||{}).filter(([key])=>!revisionFields.has(key)&&key!=='ref'&&key!=='commit_sha'&&key!=='attempt'&&key!=='attempt_id'))]);
}
function workspaceKey(scope){
 if(scope?.revision_scope!=='workspace'||!['project','project_id','workspace_id'].some(k=>scope.ids[k]?.length))return null;
 return hash([Object.fromEntries(Object.entries(scope.ids).filter(([k])=>!revisionFields.has(k)&&!['target','target_id','ref','commit_sha'].includes(k))),scope.roots]);
}
function currentWorkspaceRevision(scope,heads){
 const key=workspaceKey(scope),latest=key&&heads.get(key);
 return !latest?scope:{...scope,ids:{...scope.ids,...Object.fromEntries(Object.entries(latest.ids).filter(([k])=>revisionFields.has(k)))}};
}
module.exports={inspect,compatible,covers,combine,writeKey,workspaceKey,currentWorkspaceRevision};

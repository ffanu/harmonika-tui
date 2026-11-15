'use strict';
// Mechanics only: callers must never log matched text or original payloads.
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const {createHash}=require('node:crypto');
const spanCache=new Map();let spanSlots=0,activeBudget=null;
function createBudget(){return {remaining:24,deadline:Date.now()+1200};}
function withBudget(run){const old=activeBudget;activeBudget=createBudget();try{return run();}finally{activeBudget=old;}}
const MASK = '[REDACTED_SECRET]';
const KEY = /^(?:authorization|proxy_authorization|cookies?|set_cookie|passwords?|passwd|pwd|(?:[a-z0-9]+_)*(?:api_key|apikey|access_token|refresh_token|secret_key|client_secret|bot_token|private_key|credential|credentials|password|passwd|secret|token|cookie))$/i;
const ENV_REFERENCE = /^(?:process\.env\.[A-Z][A-Z0-9_]*|os\.environ\[['"][A-Z][A-Z0-9_]*['"]\]|(?:os\.getenv|os\.environ\.get|getenv|System\.getenv)\(['"][A-Z][A-Z0-9_]*['"]\)|\$\{[A-Z][A-Z0-9_]*\}|''|""|null|None|undefined)$/;
// Recognize only a scheme literal followed by a variable/property reference.
// No literal token, function call, quoted lookup or trailing concatenation.
const BEARER_REFERENCE = /^\s*\+\s*[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[[A-Za-z_$][\w$]*\])*\s*(?=[,};])/;
const BEARER_TEMPLATE_REFERENCE = /^ \$\{\s*[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[[A-Za-z_$][\w$]*\])*\s*\}`\s*(?=[,};])/;
function pythonReferenceSpans(source,budget){
  if(source.length>262144||!/(?:^|\n)\s*(?:\d+: ?)?\s*(?:from |import |def |class )/.test(source))return [];
  const key=createHash('sha256').update(source).digest('hex');
  if(spanCache.has(key)){const value=spanCache.get(key);spanCache.delete(key);spanCache.set(key,value);return value;}
  if(budget.remaining--<=0||Date.now()>budget.deadline)throw Error('tool_input_complexity');
  try{
    const raw=execFileSync('/usr/bin/python3',['-I',path.join(__dirname,'python-reference-spans.py')],{input:source,encoding:'utf8',timeout:1000,maxBuffer:65536,stdio:['pipe','pipe','ignore']});
    const spans=JSON.parse(raw);const result=Array.isArray(spans)&&spans.length<=2048?spans.filter(x=>Array.isArray(x)&&x.length===2&&x.every(Number.isSafeInteger)&&x[0]>=0&&x[1]>=x[0]&&x[1]<=source.length):[];
    spanCache.set(key,result);spanSlots+=result.length;
    while(spanCache.size>128||spanSlots>16384){const first=spanCache.keys().next().value;spanSlots-=spanCache.get(first).length;spanCache.delete(first);}
    return result;
  }catch{return [];}
}
function sourceReference(match,prefix,offset,text,spans){
  const begin=offset+prefix.length;
  const trimmed=match.slice(prefix.length).replace(/[}\])]+$/,'');
  const end=begin+trimmed.length;
  if(spans.some(([a,b])=>begin>=a&&end<=b))return true;
  // Limited JS/HTML source references, not raw Cookie/query/header values.
  if(/(?:^|\n)\s*(?:\d+: ?)?\s*(?:from |import |def |class )/.test(text)||!/(?:\b(?:const|let|function)\s|<script\b)/.test(text)||/[?&]/.test(prefix))return false;
  let value=trimmed,tail=text.slice(end);
  const referencePrefix=match.slice(prefix.length).match(/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[[A-Za-z_$][\w$]*\])*/);
  if(referencePrefix&&!/^document\.(?:getElementById|querySelector)\(/.test(match.slice(prefix.length))){value=referencePrefix[0];tail=text.slice(begin+value.length);}
  const ref=/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[[A-Za-z_$][\w$]*\])*$/;
  const dom=/^document\.(?:getElementById|querySelector)\((['"])(?:#?(?:password|username|pass|pw))\1\)\.value$/;
  const bindings=[],tainted=new Set(),parameters=new Set();
  const assignment=/(?:^|[;{}\n])\s*(?:(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*=(?!=)([^;\n]*)/g;
  let found;
  while((found=assignment.exec(text)))bindings.push([found[1],found[2].trim()]);
  const domNode=/^document\.(?:getElementById|querySelector)\((['"])[#A-Za-z0-9_-]+\1\)(?:\.value)?$/;
  const fnParams=/(?:function[^()]*|)\(([^()]*)\)\s*(?:=>|\{)/g;
  while((found=fnParams.exec(text)))for(const name of found[1].split(',').map(x=>x.trim()))if(/^[A-Za-z_$][\w$]*$/.test(name))parameters.add(name);
  // Destructuring/iteration and property writes are conservative, never inferred safe.
  const iteration=/\bfor\s*\(\s*(?:(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s+(?:of|in)\b/g;
  while((found=iteration.exec(text)))tainted.add(found[1]);
  const propertyWrite=/\b([A-Za-z_$][\w$]*)(?:\.[A-Za-z_$][\w$]*|\[[^\]\n]+\])+\s*=(?!=)/g;
  while((found=propertyWrite.exec(text)))tainted.add(found[1]);
  for(let pass=0;pass<32;pass++){
    const before=tainted.size;
    for(const [name,rhs] of bindings){
      const pure=ref.test(rhs)||domNode.test(rhs);
      const base=rhs.split(/[.\[]/)[0];
      if(!pure||tainted.has(base))tainted.add(name);
    }
    if(tainted.size===before)break;
    if(pass===31)return false;
  }
  const base=value.split(/[.\[]/)[0];
  const bare=!/[.\[]/.test(value);
  return (dom.test(value)||ref.test(value)&&!tainted.has(base)&&(!bare||bindings.some(([name])=>name===base)||parameters.has(base)))&&/^\s*(?:[,};)]|$)/.test(tail);
}
function scrubAssignment(match, prefix, offset, text, spans=[]) {
  const value=match.slice(prefix.length);
  if (ENV_REFERENCE.test(value)||sourceReference(match,prefix,offset,text,spans)) return match;
  if (/^authorization["']?\s*:\s*$/i.test(prefix)) {
    const tail=text.slice(offset+match.length);
    if (/^(?:'Bearer '|"Bearer ")$/.test(value) && BEARER_REFERENCE.test(tail)) return match;
    if (value==='`Bearer' && BEARER_TEMPLATE_REFERENCE.test(tail)) return match;
  }
  return prefix+MASK;
}
function scrubText(value,stats=null,budget=activeBudget||createBudget()) {
  const original=value;
  value=value
    .replace(/-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|$)/g, MASK)
    .replace(/\b(?:sk-(?:proj-|svcacct-)?|hai_live_|hst_|hgw_)[A-Za-z0-9_-]{16,}\b/g, MASK)
    .replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+=*/gi, 'Bearer ' + MASK)
    .replace(/\b[0-9]{6,12}:[A-Za-z0-9_-]{30,}\b/g, MASK)
    .replace(/(https?:\/\/)[^\s/:@]+:[^\s/@]+@/gi, '$1' + MASK + '@');
  if(stats&&value!==original)stats.known_format=(stats.known_format||0)+1;
  const needsReferences=/(?:[?&]|\b)(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|authorization|cookie|secret[_-]?key)[\"']?\s*[=:]/i.test(value);
  const spans=needsReferences?pythonReferenceSpans(value,budget):[];
  return value
    .replace(/((?:[?&]|\b)(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|authorization|cookie|secret[_-]?key)["']?\s*[=:]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s&,;\r\n]+)/gi, (match,prefix,offset,text)=>{
      const result=scrubAssignment(match,prefix,offset,text,spans);
      if(stats&&result!==match){
        const rhs=match.slice(prefix.length);
        const kind=/^=/.test(rhs)?'comparison_operator':/^[fF]['"]/.test(rhs)?'format_expression':/^['"](?:sid|session|session_id)=['"]$/.test(rhs)?'cookie_prefix_literal':/^['"`]/.test(rhs)?'quoted_literal':rhs.includes('(')?'call_expression':'other_expression';
        stats[kind]=(stats[kind]||0)+1;
        if(kind==='call_expression'){
          const form=/\bSimpleCookie\(/.test(rhs)?'cookie_constructor':/\.(?:output|OutputString)\(/.test(rhs)?'cookie_formatter':/\.get\(/.test(rhs)?'mapping_lookup':/\bself\.body\(/.test(rhs)?'request_body':'other_call';
          stats[form]=(stats[form]||0)+1;
        }
        if(!spans.length)stats.no_reference_spans=(stats.no_reference_spans||0)+1;
        if(!/(?:^|\n)\s*(?:\d+: ?)?\s*(?:from |import |def |class )/.test(text))stats.no_python_source_marker=(stats.no_python_source_marker||0)+1;
      }
      return result;
    })
    .replace(/((?:^|\s)--(?:password|passwd|token|api-key|secret)(?:=|\s+))(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s]+)/gi, '$1' + MASK)
    .replace(/(\b(?:Cookie|Set-Cookie):\s*)[^\r\n]+/gi, '$1' + MASK);
}
function scrub(value,stats=null,budget=activeBudget||createBudget()) {
  let nodes = 0;
  function walk(item, depth) {
    if (++nodes > 100000 || depth > 24) throw Error('tool_input_complexity');
    if (typeof item === 'string') return scrubText(item,stats,budget);
    if (Array.isArray(item)) return item.map(v => walk(v, depth + 1));
    if (item && typeof item === 'object') {
      const out = Object.create(null);
      for (const [key, val] of Object.entries(item)) {
        if (KEY.test(key.replace(/([a-z0-9])([A-Z])/g, '$1_$2').replace(/[.-]/g, '_')) && typeof val !== 'boolean' && val != null) {out[key] = MASK;if(stats)stats.structured_field=(stats.structured_field||0)+1;}
        else out[key] = walk(val, depth + 1);
      }
      return out;
    }
    return item;
  }
  return walk(value, 0);
}
function scrubContent(value,budget=activeBudget||createBudget()) {
  if (typeof value !== 'string') return scrub(value,null,budget);
  // Tool output often wraps secrets in JSON text; preserve valid JSON structure.
  try {
    const parsed = JSON.parse(value), clean = scrub(parsed,null,budget);
    return JSON.stringify(clean) === JSON.stringify(parsed) ? value : JSON.stringify(clean);
  }
  catch (e) { if (e.message === 'tool_input_complexity') throw e; return scrubText(value,null,budget); }
}
function assertNoSecrets(value) {
  const stats=Object.create(null);
  if (JSON.stringify(scrub(value,stats)) !== JSON.stringify(value)){
    console.warn(JSON.stringify({event:'credential_firewall_reject',mechanics:stats}));
    throw Error('tool_secret_reference_required');
  }
}
// UI-MAP is a typed registry of symbolic DOM IDs. It cannot carry credential values.
// Only a full write to this artifact uses this parser; other tools and paths keep
// the normal credential checks. Raw/duplicate/escaped keys fail closed.
function selectorMapValidationArgs(name,args){
  if(!/^(?:write|write_file|create_file)$/.test(name)||!args||typeof args!=='object'||Array.isArray(args)||typeof args.path!=='string'||args.path.replace(/\\/g,'/').split('/').pop()!=='UI-MAP.json')return args;
  const deny=()=>{throw Error('tool_secret_reference_required');};
  if(typeof args.content!=='string'||args.content.length>16384)deny();
  let parsed;try{parsed=JSON.parse(args.content)}catch{deny()}
  if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))deny();
  const keys=Object.keys(parsed),rawKeys=[...args.content.matchAll(/"([a-z][a-z0-9_]{0,63})"\s*:/g)].map(m=>m[1]);
  if(!keys.length||keys.length>64||rawKeys.length!==keys.length||new Set(rawKeys).size!==keys.length)deny();
  for(const key of keys){
    const value=parsed[key];
    if(!/^[a-z][a-z0-9_]{0,63}$/.test(key)||typeof value!=='string'||!/^#[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(value)||scrubText(value)!==value)deny();
    if(KEY.test(key)&&!(key==='login_password'&&value==='#password'))deny();
  }
  const validation=Object.assign(Object.create(null),args);validation.content='';return validation;
}
function assertToolRequest(name, args) {
  assertNoSecrets(selectorMapValidationArgs(name,args));
  if (JSON.stringify(args).includes(MASK)) throw Error('tool_secret_reference_required');
  // Defense in depth, not a shell sandbox. Dedicated secret connectors return refs.
  const fields = new Set(['path','file_path','source','target','command','commands','cmd','code','script']);
  const commandFields = new Set(['command','commands','cmd','code','script']);
  const shell = /(?:exec|shell|bash|terminal|run_commands)/i.test(name);
  function inspect(text, command) {
    if (typeof text === 'string' && /(?:^|[\\/\s'\"])(?:\.ssh|\.aws|\.gnupg|secrets|credentials)(?:[\\/]|$)|(?:^|[\\/\s'\"])\.env(?!\.(?:example|sample|template)\b)(?:\b|$)|\bid_(?:rsa|ed25519|ecdsa(?:_sk)?|dsa)\b|\/etc\/(?:shadow|gshadow)\b|\b(?:credentials\.json|server\.key)\b/i.test(text)) throw Error('tool_secret_reference_required');
    if (shell && command && typeof text === 'string' && /\bprintenv\b|(?:^|[;&|])\s*env\s*(?:$|[;&|])/.test(text)) throw Error('tool_secret_reference_required');
  }
  function walk(value, field = '') {
    field=field.replace(/([a-z0-9])([A-Z])/g,'$1_$2').toLowerCase();
    if (typeof value === 'string') { if (fields.has(field)) inspect(value, commandFields.has(field)); return; }
    if (Array.isArray(value)) { for (const item of value) walk(item, field); return; }
    if (value && typeof value === 'object') for (const [key,item] of Object.entries(value)) walk(item, key);
  }
  walk(args);
}
module.exports = {scrub, scrubText, scrubContent, assertNoSecrets, assertToolRequest, MASK, createBudget, withBudget};

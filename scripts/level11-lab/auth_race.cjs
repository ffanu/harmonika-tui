const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),requests=[];
function element(){return {value:'',dataset:{},children:[],textContent:'',replaceChildren(...xs){this.children=xs;this.textContent=''},append(...xs){this.children.push(...xs)},querySelector(s){return this.children.find(x=>'.'+x.className===s)||null}}}
function node(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}
const context=vm.createContext({document:{getElementById:node,querySelector:s=>node(s.slice(1)),createElement:element},fetch:(path,options)=>new Promise(resolve=>requests.push({path,options,resolve})),crypto:{randomUUID:()=> 'auth-race'},setTimeout,clearTimeout,console});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const flush=async()=>{for(let i=0;i<40;i++)await Promise.resolve()};
function reply(r,body,ok=true){assert.ok(r,'request must exist');r.used=true;r.resolve({ok,json:async()=>body})}
function pending(path,method='GET'){return requests.find(r=>!r.used&&r.path===path&&r.options.method===method)}
(async()=>{
 reply(pending('/p/one/tickets'),{error:'unauthorized'},false);await flush();
 node('tickets').append(element());const logout=node('logout').onclick();await flush();const old=pending('/logout','POST');assert.ok(old);assert.equal(node('tickets').children.length,0,'logout clears private view before transport');
 node('username').value='beta';node('password').value='synthetic-form-input';const login=node('login-form').onsubmit({preventDefault(){}});await flush();assert.ok(!pending('/login','POST'),'new login must wait for pending logout transport');
 reply(old,{});await logout;await flush();const fresh=pending('/login','POST');assert.ok(fresh,'new login must run after logout');reply(fresh,{});await flush();reply(pending('/p/one/tickets'),{tickets:[]});await login;assert.equal(node('workspace').hidden,false);
 const first=vm.runInContext('load()',context);await flush();const stale=pending('/p/one/tickets');stale.used=true;const second=vm.runInContext('load()',context);await flush();const latest=pending('/p/one/tickets');reply(latest,{tickets:[{id:'fresh',title:'Fresh list',status:'open',revision:1}]});await second;
 reply(stale,{tickets:[{id:'stale',title:'Stale list',status:'open',revision:1}]});await first;assert.equal(node('tickets').children[0].dataset.id,'fresh','older same-scope list must not overwrite current list');console.log('INDEPENDENT_AUTH_AND_LIST_RACE_PASS');
})().catch(e=>{console.error(e);process.exitCode=1});

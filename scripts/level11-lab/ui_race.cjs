const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),requests=[],timers=[];
function element(){return {value:'',dataset:{},children:[],textContent:'',replaceChildren(...xs){this.children=xs;this.textContent=''},append(...xs){this.children.push(...xs)},querySelector(s){return this.children.find(x=>'.'+x.className===s)||null}}}
function node(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}
const context=vm.createContext({document:{getElementById:node,querySelector:s=>node(s.slice(1)),createElement:element},fetch:(path,options)=>new Promise(resolve=>requests.push({path,options,resolve})),crypto:{randomUUID:()=> 'independent-race'},setTimeout:fn=>timers.push(fn),console});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve()};
function reply(r,body,ok=true){assert.ok(r,'request must exist');r.resolve({ok,json:async()=>body})}
(async()=>{
 reply(requests.shift(),{error:'unauthorized'},false);await flush();
 node('title').value='Pending ticket';node('units').value='2';
 const ticket=node('ticket-form').onsubmit({preventDefault(){}}),create=requests.find(r=>r.options.method==='POST'&&r.path.endsWith('/tickets'));
 const job=node('job-form').onsubmit({preventDefault(){}});
 reply(requests.find(r=>r.path.endsWith('/jobs')),{job:{id:'abc',state:'running',done:1,units:2}});await job;
 const row=node('jobs').children[0];assert.equal(row.dataset.id,'abc');assert.equal(timers.length,1);
 reply(create,{});await flush();const get=requests.filter(r=>r.options.method==='GET'&&r.path.endsWith('/tickets')).at(-1);assert.ok(get,'ticket refresh must start');
 assert.ok(node('jobs').children.includes(row),'same-scope ticket refresh must retain admitted job');
 reply(get,{tickets:[]});await ticket;await flush();assert.ok(node('jobs').children.includes(row),'same-scope refreshed ticket response must retain job');
 const poll=timers.shift()();await flush();reply(requests.filter(r=>r.options.method==='GET'&&r.path.endsWith('/jobs/abc')).at(-1),{job:{id:'abc',state:'completed',done:2,units:2}});await poll;
 assert.ok(row.children.some(x=>x.className==='download'),'completed job must expose download');console.log('INDEPENDENT_UI_RACE_PASS');
})().catch(e=>{console.error(e);process.exitCode=1});

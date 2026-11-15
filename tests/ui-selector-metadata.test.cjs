'use strict';
const test=require('node:test'),a=require('node:assert/strict'),f=require('../backend/planner/secret-firewall.cjs');
const map={login_username:'#username',login_password:'#password',login_submit:'#login-submit',nav_dashboard:'#nav-dashboard',customer_name:'#customer-name'};
const req=content=>({path:'/project/UI-MAP.json',content:typeof content==='string'?content:JSON.stringify(content,null,2)});
test('complete UI selector registry carries symbolic DOM IDs rather than credentials',()=>{const args=req(map),before=JSON.stringify(args);a.doesNotThrow(()=>f.assertToolRequest('write',args));a.equal(JSON.stringify(args),before);});
test('selector metadata permission never applies to another artifact or shell',()=>{a.throws(()=>f.assertToolRequest('write',{...req(map),path:'/project/secrets.json'}),/tool_secret_reference_required/);a.throws(()=>f.assertToolRequest('shell',req(map)),/tool_secret_reference_required/);a.throws(()=>f.assertToolRequest('write',{...req(map),path:'/project/.ssh/UI-MAP.json'}),/tool_secret_reference_required/);});
test('raw values, secret-bearing fields and synthetic known-format keys remain rejected',()=>{
 for(const value of [{...map,login_password:'inline-private'},{...map,login_password:'#Admin130290'},{...map,api_token:'#access'},{...map,cookie:'#session'}, {...map,other:'#hai_live_abcdefghijklmnopqrstuv'}, {...map,other:'https://example.invalid'},{...map,other:{value:'#field'}}])a.throws(()=>f.assertToolRequest('write',req(value)),/tool_secret_reference_required/);
 a.throws(()=>f.assertToolRequest('write',{...req(map),password:'inline-private'}),/tool_secret_reference_required/);
});
test('duplicate keys and escaped ambiguous property names cannot hide raw assignments',()=>{
 for(const content of ['{"login_password":"inline-private","login_password":"#password"}','{"login_\\u0070assword":"#password"}','{"login_password":"#password","__proto__":"#field"}'])a.throws(()=>f.assertToolRequest('write',req(content)),/tool_secret_reference_required/);
});
test('Windows selector map paths normalize separators while private paths stay denied',()=>{
 a.doesNotThrow(()=>f.assertToolRequest('write',{...req(map),path:String.raw`C:\work\UI-MAP.json`}));
 a.throws(()=>f.assertToolRequest('write',{...req(map),path:String.raw`C:\work\.ssh\UI-MAP.json`}),/tool_secret_reference_required/);
});

'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {compact,dispatch}=require('../runtime/tools/ssh-mcp.cjs');
test('all remote capabilities fit within native interactive gateway budget',()=>{
 assert.equal(compact.length,7);assert.ok(15+8+compact.length+1+1<=32);
 for(const t of compact){assert.equal(t.inputSchema.type,'object');assert.match(t.name,/^[A-Za-z0-9_-]{1,64}$/);}
});
test('Telnet and VNC dispatch retain each operation without passing operation to worker',()=>{
 for(const [name,operations]of [['telnet',['connect','exec']],['vnc',['connect','capture','control']]])for(const operation of operations){const value=dispatch(name,{operation,target:'fixture'});assert.equal(value.skill,name+'.'+operation);assert.deepEqual(value.input,{target:'fixture'});}
 assert.equal(dispatch('vnc',{operation:'delete'}),null);
});
test('legacy names remain callable without increasing advertised tools',()=>{assert.equal(dispatch('vnc_capture',{target:'fixture'}).skill,'vnc.capture');assert.ok(!compact.some(t=>t.name==='vnc_capture'));});

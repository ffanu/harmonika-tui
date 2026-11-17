'use strict';
// Synthetic client-reported receipts only. No processes, network or user files.
const {test}=require('node:test'),assert=require('node:assert/strict');
const continuity=require('../backend/planner/task-continuity.cjs');
// Operator proof source uses POSIX secure file primitives at module load.
const agent=process.platform==='win32'?null:require('../backend/planner/agent-controller.cjs');
const TAP='TAP version 13\n# tests 2\n# pass 2\n# fail 0\n';
const UNIT='..\n----------------------------------------------------------------------\nRan 2 tests in 0.001s\n\nOK\n';
function receipt(output=TAP,metadata={status:'completed',exit_code:0,truncated:false}){return {output,metadata};}
function inspect(command,body=receipt(),name='shell',args={}){return continuity.inspect(name,{command,workdir:'C:\\Work\\Fixture',...args},typeof body==='string'?body:JSON.stringify(body));}
function history(entries,request='Perbaiki file aplikasi dan jalankan tes.'){return {messages:[{role:'user',content:request},...entries.flatMap(([name,args,result],i)=>[{role:'assistant',tool_calls:[{id:'r'+i,type:'function',function:{name,arguments:JSON.stringify(args)}}]},{role:'tool',tool_call_id:'r'+i,content:typeof result==='string'?result:JSON.stringify(result)}])]};}
const complete=ids=>({content:'Tes lulus: 2 tes.',tool_calls:[],task_state:{phase:'complete',remaining:[],next_step:'',evidence_call_ids:ids}});

test('actual native shell/bash command shapes accept structured exit0 positive TAP/unittest summaries',()=>{
 for(const name of ['shell','bash'])for(const command of ['node --test','node.exe --test','npm.cmd test','npm.exe run test','python.exe -m unittest','python3.exe -m pytest','py -3 -m unittest','py.exe -3.12 -m unittest']){
  const value=inspect(command,receipt(command.includes('unittest')?UNIT:TAP),name);
  assert.equal(value.test,true,command);assert.equal(value.test_count,2,command);assert.equal(value.test_passed,true,command);assert.equal(value.execution_failed,false);
 }
});

test('native explicit count metadata requires matching positive counts and a process exit',()=>{
 const good={metadata:{status:'completed',exitCode:0,collected:2,passed:2,failed:0}};
 assert.equal(inspect('node.exe --test',good).test_passed,true);
 for(const body of [{metadata:{collected:2,passed:2}},{metadata:{exitCode:0,collected:2,passed:1}},{metadata:{exitCode:0,collected:0,passed:0}},{metadata:{exitCode:0,collected:2,passed:2,failed:1}},{collected:2,passed:2,exit_code:0,metadata:{collected:3,passed:3,exit_code:0}}])assert.equal(inspect('node.exe --test',body).test_passed,false);
});

test('bare TAP/source content never implies verified native process exit',()=>{
 for(const body of [TAP,UNIT,'',receipt(TAP,{}),receipt(TAP,{exit_code:null}),receipt(TAP,{exit_code:'unknown'}),receipt('',{exit_code:0})])assert.equal(inspect('node --test',body).test_passed,false);
 for(const name of ['read','read_file','fixture.read','opaque'])assert.equal(inspect('node --test',receipt(),name).test_passed,false);
});

test('nonzero and conflicting exits cannot be hidden by a passing summary or top-level exit0',()=>{
 for(const metadata of [{exit_code:1},{exitCode:-1},{exit_status:'2'},{exit:3},{exit_code:0,metadata:{exitCode:4}}]){
  const value=inspect('node.exe --test',receipt(TAP,metadata));assert.equal(value.test_passed,false);assert.equal(value.test_failed,true);
 }
 assert.equal(inspect('node --test',{output:TAP,exit_code:0,metadata:{exit_code:1}}).test_passed,false);
});

test('pending/unknown/partial/truncated/timeout/signal receipts cannot verify completion',()=>{
 for(const extra of [{status:'running'},{status:'outcome_unknown'},{status:'unrecognized'},{partial:true},{truncated:true},{complete:false},{timeout:true},{killed:true},{signal:'SIGTERM'},{metadata:{truncated:true,exit_code:0}}]){
  assert.equal(inspect('node --test',receipt(TAP,{exit_code:0,...extra})).test_passed,false,JSON.stringify(extra));
 }
});

test('no zero-test or failed-test summary becomes passing evidence via exit0',()=>{
 for(const output of ['# tests 0\n# pass 0\n# fail 0\n','# tests 2\n# pass 1\n# fail 1\n','Ran 0 tests in 0s\n\nOK\n','Ran 2 tests in 0s\n\nFAILED (failures=1)\n'])assert.equal(inspect('node --test',receipt(output)).test_passed,false);
});

test('known Windows executable forms preserve Cline command-envelope correlation',()=>{
 for(const command of ['node.exe --test','npm.cmd test','py -3 -m unittest']){
  const body=[{query:command,success:true,result:command.includes('unittest')?UNIT:TAP}];
  assert.equal(continuity.inspect('run_commands',{commands:[command]},body).test_passed,true);
  assert.equal(continuity.inspect('run_commands',{commands:[command+' extra']},body).test_passed,false);
  assert.equal(continuity.inspect('run_commands',{commands:[command]},[...body,...body]).test_passed,false);
 }
});

test('native command recognizer refuses echoed tests, pipelines and masked/compound results',()=>{
 for(const command of ['echo node.exe --test','type result.json','npm.cmd run build','node.exe --test; echo pass','node --test && echo pass','node --test | echo pass','node --test\necho pass'])assert.equal(inspect(command).test,false,command);
});

test('Windows cd scope is normalized without case folding or bypassing prefix scope',()=>{
 const command='cd /d "C:\\Work\\Fixture" && node.exe --test';
 const value=inspect(command);assert.equal(value.test_passed,true);assert.equal(value.scope.explicit_root,true);
 for(const command of ['npm.cmd test --prefix C:\\Other','node.exe --test --cwd C:\\Other','node.exe --test -C C:\\Other']){
  const value=inspect(command);assert.equal(value.scope.ambiguous,true,command);assert.equal(value.test_passed,false,command);
 }
});

test('native receipt correlation and revision conflicts remain rejected',()=>{
 const conflict=inspect('node.exe --test',receipt(TAP,{exit_code:0,project:'foreign',revision:'old',job_id:'foreign-job'}),'shell',{project:'current',revision:'new',job_id:'current-job'});
 assert.equal(conflict.scope_conflict,true);assert.equal(conflict.correlation_conflict,true);assert.equal(conflict.test_passed,false);
 const stale=inspect('node.exe --test',receipt(TAP,{exit_code:0,revision:'old'}),'shell',{revision:'new'});
 assert.equal(stale.scope_conflict,true);assert.equal(stale.test_passed,false);
});

test('controller accepts current native verification and rejects stale/foreign/signed-proof gaps',{skip:!agent},()=>{
 const write=['write_file',{path:'C:\\Work\\Fixture\\src.js',workdir:'C:\\Work\\Fixture',content:'fixed'},{ok:true,revision:'new'}];
 const current=['shell',{command:'node.exe --test',workdir:'C:\\Work\\Fixture'},receipt(TAP,{status:'completed',exit_code:0,revision:'new'})];
 assert.doesNotThrow(()=>agent.check(complete(['r1']),history([write,current])));
 for(const command of ['cd /d C:\\Other && node.exe --test','npm.cmd test --prefix C:\\Other']){
  const foreign=['shell',{command,workdir:'C:\\Work\\Fixture'},receipt(TAP,{exit_code:0,revision:'new'})];
  assert.throws(()=>agent.check(complete(['r1']),history([write,foreign])),e=>e.decisionReason==='unverified_completion');
 }
 const stale=['shell',{command:'node.exe --test',workdir:'C:\\Work\\Fixture'},receipt(TAP,{exit_code:0,revision:'old'})];
 assert.throws(()=>agent.check(complete(['r1']),history([write,stale])),e=>e.decisionReason==='unverified_completion');
 assert.throws(()=>agent.check(complete(['r0']),history([current,write])),e=>e.decisionReason==='unverified_completion');
 assert.throws(()=>agent.check(complete(['r1']),history([write,current]),()=>({required:['tests'],checks:[]})),e=>e.decisionReason==='unverified_completion');
});

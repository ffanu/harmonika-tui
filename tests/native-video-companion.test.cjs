const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createAdapter,DESCRIPTION}=require('../runtime/video-control/native-video.cjs');

test('live quality guidance replaces stale 288p limits without changing tool schema',()=>{
 const original={name:'video',description:'old preview',inputSchema:{type:'object'}};
 const adapter=createAdapter({createTools:()=>({listTools:()=>[original],close(){}})});
 assert.equal(adapter.listTools()[0].inputSchema,original.inputSchema);
 assert.equal(original.description,'old preview');
 assert.match(DESCRIPTION,/1280x720/);assert.doesNotMatch(DESCRIPTION,/512x288/);
 assert.match(DESCRIPTION,/concise English/);assert.match(DESCRIPTION,/supplied reference/);
});

test('download reports verified backend quality and keeps saved artifact result',async()=>{
 const calls=[];
 const adapter=createAdapter({createTools:()=>({listTools:()=>[],close(){},async call(name,args){
  calls.push(args.action);
  return args.action==='download'?{ok:true,output_file:'/fixture.mp4',sha256:'a'.repeat(64)}:
   {ok:true,state:'completed',result:{width:1280,height:720,quality:'high',audio:false,models:['Wan 2.2']}};
 }})});
 const result=await adapter.call('video',{action:'download',job_id:'a'.repeat(32)});
 assert.deepEqual(calls,['download','status']);
 assert.equal(result.output_file,'/fixture.mp4');assert.equal(result.height,720);assert.equal(result.quality,'high');
});

test('failed submission remains an explicit error and never triggers a fallback',async()=>{
 let n=0;const adapter=createAdapter({createTools:()=>({listTools:()=>[],close(){},async call(){n++;return {ok:false,error:{code:'high_video_unavailable'}}}})});
 const out=await adapter.call('video',{action:'generate',duration_seconds:10,prompt:'fixture'});
 assert.equal(n,1);assert.equal(out.error.code,'high_video_unavailable');
});

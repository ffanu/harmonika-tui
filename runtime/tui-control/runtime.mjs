import {createMonitor} from './model.mjs';
import {decideRetry} from './retry.mjs';

const ID=/^[A-Za-z0-9_-]{1,160}$/;
const PHASES=['plan','write','install','build','test'];
const RPC={id:'harmonika.control',methods:{get:{input:{type:'object',properties:{sessionID:{type:'string',minLength:1,maxLength:160}},required:['sessionID'],additionalProperties:false},output:{type:'object',additionalProperties:true}}},events:{}};
export {RPC};

export default {
  id:'harmonika.control.runtime',
  async setup(ctx) {
    const monitor=createMonitor();
    const cleanup=[];
    try {
      const rpc=await ctx.rpc.register(RPC,{get:async({sessionID})=>{if(!ID.test(sessionID))throw Error('invalid_session_id');return monitor.get(sessionID)}});
      cleanup.push(()=>rpc.dispose());
      const retry=await ctx.session.hook('retry',event=>{
        const state=monitor.get(event.sessionID);
        const result=decideRetry(event.error,event.attempt,event.decision,state.step_emitted||state.step_tool_called);
        if(!result.retry)event.decision={retry:false};
        monitor.recordRetry(event.sessionID,result);
      },{providerID:'harmonika'});
      cleanup.push(()=>retry.dispose());
      const tools=await ctx.tool.transform(editor=>editor.add({
        name:'harmonika_workflow_phase',
        description:'Laporkan tahap pekerjaan panjang sebelum mengerjakannya: plan, write, install, build, test. Hanya metadata; tidak mengeksekusi perintah dan tidak membuktikan tes/deploy berhasil. Jangan laporkan selesai tanpa hasil tool nyata.',
        input:{type:'object',properties:{phase:{type:'string',enum:PHASES}},required:['phase'],additionalProperties:false},
        options:{codemode:false},
        async execute(input,context) {
          if(!input||Object.keys(input).length!==1||!PHASES.includes(input.phase))throw Error('invalid_workflow_phase');
          monitor.declarePhase(context.sessionID,input.phase);
          return {content:'Tahap '+input.phase+' dilaporkan; keberhasilan belum diverifikasi.',metadata:{harmonika_phase:input.phase}};
        },
      }));
      cleanup.push(()=>tools.dispose());
      const abort=new AbortController();
      const events=ctx.event.subscribe({signal:abort.signal,types:['session.execution.started','session.execution.succeeded','session.execution.failed','session.execution.interrupted','session.step.started','session.text.delta','session.tool.input.started','session.tool.called','session.tool.success','session.tool.failed','session.tool.progress','session.retry.scheduled']});
      const iterator=events[Symbol.asyncIterator]();
      const watch=(async()=>{try{while(!abort.signal.aborted){const item=await iterator.next();if(item.done)break;monitor.accept(item.value)}}catch{}})();
      cleanup.push(async()=>{abort.abort();await iterator.return?.();await watch});
      return async()=>{for(const dispose of cleanup.reverse())await dispose()};
    } catch(error) {
      for(const dispose of cleanup.reverse())await dispose();
      throw error;
    }
  },
};

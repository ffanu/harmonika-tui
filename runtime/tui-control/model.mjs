'use strict';
const STATES = new Set(['queued','thinking','tool_running','running','waiting_permission','waiting_input','retrying','completed','failed','interrupted','idle']);
const CODE = /^[a-z][a-z0-9_.-]{0,63}$/;
const ID = /^[A-Za-z0-9_-]{1,160}$/;
const TOOL = /^[A-Za-z][A-Za-z0-9_.:-]{0,79}$/;
const PHASES=['plan','write','install','build','test','finish'];
const phaseLabels={plan:'Memahami / rencana',write:'Menulis kode',install:'Instalasi',build:'Build',test:'Tes',finish:'Akhir respons'};
function toolPhase(name) {
  // Exact tool identities only: never inspect commands, paths or Code Mode text.
  if(/^(?:read|read_file|read_code|glob|grep|list|code\.read|code\.search|code\.grep)$/.test(name))return 'plan';
  if(/^(?:write|write_file|write_code|edit|edit_file|apply_patch|code\.write|code\.edit|code\.patch)$/.test(name))return 'write';
  if(/^(?:pkg\.install|pkg_install|install_pkg|install_dependencies)$/.test(name))return 'install';
  if(/^(?:build\.run|build_run|run_build)$/.test(name))return 'build';
  if(/^(?:test\.run|test_run|run_tests|test\.single|test\.coverage|qa_matrix|lint\.run|type\.check)$/.test(name))return 'test';
  return null;
}
function emptyPhases(){return PHASES.map(id=>({id,status:'not_observed',source:'none'}))}
function text(value, limit=100) {
  return String(value ?? '').replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, '').slice(0,limit);
}
function createMonitor(clock=Date.now) {
  const sessions = new Map();
  function ensure(id) {
    if(typeof id!=='string'||!ID.test(id))return;
    if(!sessions.has(id)) {
      if(sessions.size>=64)sessions.delete(sessions.keys().next().value);
      sessions.set(id,{status:'idle',started_at:clock(),updated_at:clock(),emitted:false,attempt:0,error_type:null,tools:[],compacting:false,phases:emptyPhases(),active_phase:null,step_emitted:false,step_tool_called:false});
    }
    return sessions.get(id);
  }
  function phase(s,id,source) {
    const row=s.phases.find(p=>p.id===id);
    if(!row)return;
    row.status=source==='agent'?'declared':'observed';row.source=source;
    if(!['failed','interrupted','completed'].includes(s.status))s.active_phase=id;
  }
  function declarePhase(id,value) {
    if(!PHASES.slice(0,-1).includes(value))return false;
    const s=ensure(id);if(!s)return false;
    phase(s,value,'agent');s.updated_at=clock();return true;
  }
  function recordRetry(id,record) {
    const s=ensure(id);if(!s)return;
    const reasons=new Set(['credential_reference','invalid_arguments','access','quota','partial_response','native_stopped','retry_budget','timeout_budget','temporary','unknown_error']);
    if(!reasons.has(record.reason))return;
    s.retry={reason:record.reason,retry:record.retry===true,code:CODE.test(record.code??'')?record.code:'unknown',status:Number.isInteger(record.status)?record.status:null,attempt:Number.isInteger(record.attempt)?record.attempt:0};
    s.updated_at=clock();
  }
  function accept(event) {
    const type=event?.type, d=event?.data;
    if (typeof type!=='string' || !d || !ID.test(d.sessionID || '')) return;
    const accepted = new Set(['session.execution.started','session.execution.succeeded','session.execution.failed','session.execution.interrupted','session.step.started','session.tool.input.started','session.tool.called','session.tool.success','session.tool.failed','session.tool.progress','session.retry.scheduled','permission.asked','permission.replied','form.created','form.replied','form.cancelled','session.text.delta','session.compaction.started','session.compaction.ended']);
    if (!accepted.has(type)) return false;
    const s=ensure(d.sessionID);s.updated_at=clock();
    if (type==='session.execution.started') {
      Object.assign(s,{status:'thinking',started_at:clock(),emitted:false,error_type:null,attempt:0,tools:[],phases:emptyPhases(),active_phase:'plan',step_emitted:false,step_tool_called:false,retry:null,retry_at:null});
      phase(s,'plan','native');
    }
    if(type==='session.step.started')Object.assign(s,{step_emitted:false,step_tool_called:false});
    if (type==='session.text.delta') {
      s.step_emitted=true;
      if(s.emitted) return false;
      s.emitted=true; // Never read or retain delta text.
    }
    if (type==='session.execution.succeeded') s.status='completed';
    if (type==='session.execution.interrupted') s.status='interrupted';
    if (type==='session.execution.failed') {
      s.status=s.emitted?'interrupted':'failed';
      s.error_type=CODE.test(d.error?.type || '')?d.error.type:'operation_failed';
      // Never read error.message, provider response bodies, reasoning or chat text.
    }
    if (type==='session.retry.scheduled') {
      s.status=s.emitted?'interrupted':'retrying';
      s.attempt=Number.isSafeInteger(d.attempt) && d.attempt>0 && d.attempt<100 ? d.attempt : 0;
      s.retry_at=Number.isFinite(d.at)&&d.at>0?d.at:null;
    }
    if(type==='permission.asked') s.status='waiting_permission';
    if(type==='form.created') s.status='waiting_input';
    if(['permission.replied','form.replied','form.cancelled'].includes(type) && ['waiting_permission','waiting_input'].includes(s.status)) s.status='running';
    if(type==='session.compaction.started') s.compacting=true;
    if(type==='session.compaction.ended') s.compacting=false;
    if(type==='session.tool.called')s.step_tool_called=true;
    if(type==='session.tool.input.started' && ID.test(d.id || '') && TOOL.test(d.name || '')) {
      if(!s.tools.some(t=>t.id===d.id)) s.tools.push({id:d.id,name:d.name,status:'queued',started_at:clock(),provider_executed:false});
      s.tools=s.tools.slice(-40);
    }
    if (['session.tool.called','session.tool.success','session.tool.failed'].includes(type)) {
      const tool=s.tools.find(t=>t.id===d.id);
      if(tool) {
        tool.status=type.endsWith('called')?'running':type.endsWith('success')?'completed':'failed';
        // Native `executed` means provider-hosted execution, not whether a local
        // tool ran. Canonical terminal events already prove a tool result exists.
        tool.provider_executed=d.executed===true || tool.provider_executed;
        const exit=d.metadata?.exit_code;
        if(type!=='session.tool.called' && Number.isInteger(exit) && exit>=-255 && exit<=255) tool.exit_code=exit;
        const category=tool.phase??toolPhase(tool.name);
        if(category){tool.phase=category;phase(s,category,'tool_name');const row=s.phases.find(p=>p.id===category);if(tool.exit_code!=null)row.last_exit_code=tool.exit_code;}
        if(tool.name==='harmonika_workflow_phase'&&type==='session.tool.success'&&PHASES.slice(0,-1).includes(d.metadata?.harmonika_phase))phase(s,d.metadata.harmonika_phase,'agent');
        if(tool.status!=='running') tool.finished_at=clock();
      }
      if(!['interrupted','failed','completed'].includes(s.status))s.status=type.endsWith('called')?'tool_running':'thinking';
    }
    if(['session.execution.failed','session.execution.interrupted'].includes(type)) {
      for(const tool of s.tools) if(['queued','running'].includes(tool.status)) tool.status='recovery_required';
    }
    if(['session.execution.succeeded','session.execution.failed','session.execution.interrupted'].includes(type)){
      s.active_phase='finish';const row=s.phases.find(p=>p.id==='finish');row.status=s.status;row.source='native';
    }
    return true;
  }
  function get(id, running=false) {
    const value=sessions.get(id);
    if(!value) return {status:running?'running':'idle',tools:[],emitted:false,attempt:0,error_type:null,observed:false,phases:emptyPhases(),active_phase:null,step_emitted:false,step_tool_called:false};
    return {...value,tools:value.tools.map(t=>({...t})),phases:value.phases.map(p=>({...p})),retry:value.retry?{...value.retry}:null,observed:true};
  }
  return {accept,get,declarePhase,recordRetry};
}
function classifyConnection(status, session) {
  const state=status?.backend?.status || 'not_checked';
  if(['auth_required','key_not_configured','unsafe_key_file','invalid_key_file'].includes(state)) return {state:'auth_required',retry:false,code:state};
  if(['quota_exceeded','rate_limit'].includes(state)) return {state:'limited',retry:false,code:state};
  if(/auth|permission/.test(session?.error_type || '')) return {state:'auth_required',retry:false,code:'native_access_failed'};
  if(/quota|limit/.test(session?.error_type || '')) return {state:'limited',retry:false,code:'native_limit_reached'};
  if(state!=='online') return {state:state==='not_checked'?'checking':'offline',retry:true,code:state};
  if(session?.status==='interrupted') return {state:'interrupted',retry:false,code:'check_status_before_continue'};
  if(session?.status==='retrying') return {state:'retrying',retry:false,code:'native_retry_scheduled'};
  return {state:'online',retry:false,code:'connected'};
}
function backoff(failures) {return Math.min(60000,15000*2**Math.min(Math.max(0,failures),3));}
function actionRows(actions,width) {
  const limit=Math.max(8,width-2),rows=[];
  for(const action of actions) {
    const row=rows.at(-1);
    const used=row?row.reduce((n,item)=>n+item.label.length+3,0):0;
    if(!row||used+action.label.length+2>limit)rows.push([action]);
    else row.push(action);
  }
  return rows;
}
function labels(state) {
  return ({online:'Online',offline:'Offline',busy:'Sibuk',vram_full:'VRAM penuh',warming:'Menyiapkan model',unknown:'Belum diketahui',unavailable:'Belum tersedia',not_checked:'Belum diperiksa',auth_required:'Periksa akses',checking:'Memeriksa',thinking:'Memahami tugas',tool_running:'Menjalankan tool',running:'Berjalan',waiting_permission:'Menunggu izin',waiting_input:'Menunggu jawaban',retrying:'Menunggu retry native',completed:'Selesai',failed:'Gagal',interrupted:'Terputus / dihentikan',idle:'Siap',limited:'Batas layanan',queued:'Antrean',cancel_requested:'Stop diminta',cancelled:'Dibatalkan',stopped:'Dihentikan',recovery_required:'Periksa hasil terakhir',timeout:'Waktu tunggu habis',ready:'Siap',scanning:'Memindai',recovering:'Memulihkan',disabled:'Nonaktif',approval_required:'Menunggu persetujuan',expired:'Kedaluwarsa'})[state] || text(state || 'Belum diketahui',60);
}
export {createMonitor,classifyConnection,backoff,actionRows,labels,text,STATES,PHASES,phaseLabels,toolPhase};

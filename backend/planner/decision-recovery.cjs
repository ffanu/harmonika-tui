'use strict';
// Recovery regenerates an unexecuted decision. It never invokes client tools.
const REASONS = new Set(['result_file', 'result_json', 'decision_contract', 'arguments_json', 'empty_decision', 'task_contract', 'task_incomplete', 'unverified_completion', 'stalled_decision','output_budget']);
const {performance} = require('node:perf_hooks');
// Fixed mechanics only: never log an arbitrary error property or receipt value.
const DETAILS=new Set(['unobserved_evidence','invalid_completion_state','failed_or_stale_evidence','missing_waiting_evidence','operator_proof_incomplete','unsupported_success_claim','continuity_unverified']);
// All generations share this budget, leaving room below the 300s client header deadline.
const REQUEST_BUDGET_MS = 240000, ATTEMPT_LIMIT_MS = 180000;
// A latency class, never client identity, authorization or an execution timeout.
// Cline declares other tools too; require the three actual function definitions,
// allowing additional capabilities without inferring anything from client hints.
function requestPolicy(data) {
  const functions = (Array.isArray(data?.tools) ? data.tools : []).filter(t =>
    t?.type === 'function' && typeof t.function?.name === 'string'
    && t.function.parameters?.type === 'object');
  const interactive = ['read_files','run_commands','editor'].every(name =>
    functions.filter(t => t.function.name === name).length === 1);
  return interactive
    ? {profile:'interactive_tools',requestMs:90000,attemptMs:60000}
    : {profile:'general',requestMs:REQUEST_BUDGET_MS,attemptMs:ATTEMPT_LIMIT_MS};
}
function invalid(reason) {
  const error = Error('invalid_tool_response');
  error.decisionReason = REASONS.has(reason) ? reason : 'decision_contract';
  return error;
}
function parseResult(raw, validate, data) {
  let value;
  try { value = JSON.parse(raw); } catch { throw invalid('result_json'); }
  if (!value || typeof value.content !== 'string' || !Array.isArray(value.tool_calls)
      || value.tool_calls.some(c => !c || typeof c.name !== 'string' || typeof c.arguments !== 'string')) {
    throw invalid('decision_contract');
  }
  try { return validate(value, data); }
  catch (error) {
    if (REASONS.has(error.decisionReason)) throw error;
    if (error instanceof SyntaxError) throw invalid('arguments_json');
    if (error.message === 'invalid_tool_response') throw invalid('decision_contract');
    if (error.message === 'empty_tool_response') throw invalid('empty_decision');
    throw error; // Argument schema, history, secret and permission failures stay terminal.
  }
}
function transportError(error) {
  if (REASONS.has(error.decisionReason)) {const result=invalid(error.decisionReason);if(DETAILS.has(error.decisionDetail))result.decisionDetail=error.decisionDetail;return result;}
  const code = typeof error.message === 'string' && /^(?:tool_|invalid_)/.test(error.message)
    ? error.message : 'invalid_tool_response';
  const result=Error(code);
  if(['tool_engine_timeout','tool_engine_output_limit'].includes(code)&&error.decisionUnexecuted===true&&error.transportClosed===true){result.decisionUnexecuted=true;result.transportClosed=true;}
  return result;
}
function constrainSchema(schema, data, repair = '', attempt = {}) {
  const result = JSON.parse(JSON.stringify(schema));
  const calls = result.properties.tool_calls;
  const choice = data.tool_choice || 'auto';
  if (choice === 'none' || !(data.tools || []).length) calls.maxItems = 0;
  if (data.parallel_tool_calls === false) calls.maxItems = Math.min(calls.maxItems ?? 1, 1);
  if (choice === 'required' || typeof choice === 'object') calls.minItems = 1;
  if (choice && typeof choice === 'object' && typeof choice.function?.name === 'string') {
    calls.items.properties.name.enum = [choice.function.name];
  }
  if(repair==='output_budget'||attempt.outputBudget===true){
    calls.maxItems=Math.min(calls.maxItems??1,1);
    if(result.properties.content)result.properties.content.maxLength=Math.min(result.properties.content.maxLength??2000,2000);
    if(calls.items.properties.arguments)calls.items.properties.arguments.maxLength=Math.min(calls.items.properties.arguments.maxLength??12000,12000);
    const state=result.properties.task_state?.properties;
    if(state?.remaining?.items)state.remaining.items.maxLength=Math.min(state.remaining.items.maxLength??500,500);
    if(state?.next_step)state.next_step.maxLength=Math.min(state.next_step.maxLength??1000,1000);
  }
  return result;
}
function repairInstruction(reason) {
  if (!REASONS.has(reason)) return '';
  if(reason==='output_budget')return '\nOUTPUT BUDGET CORRECTION: The overflowing decision was never returned and its process is confirmed closed. Return one concise next decision: at most one tool call, arguments <=12000 characters, content <=2000, short task state. Do not copy the reference envelope, history, source read results or long reports into the answer. Split large work across normal client steps. Do not repeat completed actions or replay mutations. Keep current scope, tool choice and credential firewall.\n';
  if(['task_contract','task_incomplete','unverified_completion','stalled_decision'].includes(reason))return '\nTASK DECISION CORRECTION: The internal decision failed the task contract before any client call was returned. Return the required task_state. Continue the actual authorized unfinished task with an available tool; do not finish with a promise or plan. Cite only observed tool result IDs for completion. Inspect the results rather than assuming a passing check. For an explicitly read-only legacy reporting task, complete failed checks or a zero-test summary may be reported with phase=answer and their actual evidence IDs; do not claim positive completion, passing tests or a successful repair. This does not bypass operator proof or partial/pending/unknown outcomes. If successful test evidence is required and lacks a correlated positive count, run a short standalone test command and cite its new actual result; never replay a mutation to obtain evidence. If a prerequisite is genuinely unavailable, give a concrete blocked/waiting status and next step. Do not repeat the same unchanged check or duplicate completed actions. Respect tool_choice, permissions, credential firewall and current scope.\n';
  return '\nDECISION FORMAT CORRECTION: The previous internal decision was rejected before any client tool was returned or executed. Return one valid schema object. Use only declared function names. arguments must be a JSON-encoded object string, with newlines and quotes escaped using JSON encoding. Honor tool_choice and parallel_tool_calls; when parallel_tool_calls=false return at most one call. Reconsider the existing client results without repeating completed actions. A missing credential reference is a normal result: report it and use a configured private reference; never read or print secrets.\n';
}
async function chooseDecision(plan, data, log = () => {}, wait = ms => new Promise(r => setTimeout(r, ms)), options = {}) {
  const policy = requestPolicy(data);
  const now = options.now || (() => performance.now()), deadline = now() + policy.requestMs;
  if(policy.profile==='interactive_tools')log({event:'internal_tool_budget',profile:policy.profile,request_ms:policy.requestMs,attempt_ms:policy.attemptMs});
  let repairs = 0, unavailable = 0, timeouts = 0, overflows = 0, repair = '';
  for (let attempt = 1; attempt <= 3; attempt++) {
    const remaining = Math.floor(deadline - now());
    if (remaining <= 0) throw Error('tool_engine_timeout');
    try { return await plan(data, repair, {timeoutMs:Math.min(policy.attemptMs, remaining),outputBudget:overflows>0}); }
    catch (error) {
      const reason = REASONS.has(error.decisionReason) ? error.decisionReason : null;
      // Preserve terminal access/schema/firewall errors even at the deadline.
      if (now() >= deadline && (reason || ['tool_engine_unavailable','tool_engine_timeout','tool_engine_output_limit'].includes(error.message))) throw Error('tool_engine_timeout');
      if (reason) log({event:'internal_tool_format_error', reason, attempt,...(DETAILS.has(error.decisionDetail)?{detail:error.decisionDetail}:{})});
      if (reason && repairs === 0 && attempt < 3) {
        repairs++; repair = reason;
        log({event:'internal_tool_format_repair', reason, attempt});
        continue;
      }
      if (error.message === 'tool_engine_unavailable' && unavailable < 2 && attempt < 3) {
        unavailable++;
        log({event:'internal_tool_retry', code:'tool_engine_unavailable', attempt});
        await wait(unavailable === 1 ? 400 : 1200);
        continue;
      }
      if(error.message==='tool_engine_timeout'&&error.decisionUnexecuted===true&&error.transportClosed===true&&timeouts===0&&attempt<3){
        timeouts++;log({event:'internal_tool_retry',code:'tool_engine_timeout',attempt,decision_unexecuted:true,transport_closed:true});
        await wait(400);continue;
      }
      if(error.message==='tool_engine_output_limit'&&error.decisionUnexecuted===true&&error.transportClosed===true&&overflows===0&&attempt<3){
        overflows++;repair='output_budget';log({event:'internal_tool_retry',code:'tool_engine_output_limit',attempt,decision_unexecuted:true,transport_closed:true,bounded_output:true});
        await wait(400);continue;
      }
      throw error;
    }
  }
}
module.exports = {invalid, parseResult, transportError, constrainSchema, repairInstruction, chooseDecision, requestPolicy};

'use strict';
// Independent Harmonika implementation. No external pruning plugin required.
const {createHash} = require('node:crypto');
const firewall = require('./secret-firewall.cjs');
const BUDGET = 180000;
const MAX_INPUT = 8 * 1024 * 1024;
const size = x => JSON.stringify(x).length;
const protectedTool = /(?:exec|shell|run|write|edit|patch|install|deploy|commit|merge|delete|remove|upload|restart|verify|audit|check|health|status|test|build|receipt|payment|recover)/i;
function digest(value) { return createHash('sha256').update(value).digest('hex'); }
function excerpt(text) {
  const marker = '\n[Earlier tool output abbreviated; omitted text is NOT execution evidence. Reread before acting. sha256=' + digest(text) + ']\n';
  return text.slice(0, 4000) + marker + text.slice(-2000);
}
function content(value) {
  if (typeof value === 'string') return firewall.scrubContent(value).replace(/data:[\w/+.-]+;base64,[A-Za-z0-9+/=\r\n]+/g, '[Media omitted from decision model; use the original attachment in the media tool.]');
  if (!Array.isArray(value)) return firewall.scrub(value);
  return value.map(part => {
    if (part && ['image_url', 'input_image', 'image', 'file', 'input_file', 'document', 'audio', 'input_audio', 'video'].includes(part.type)) {
      return {type:'text', text:'[Media omitted from decision model. This is not visual analysis; use the original attachment in the media tool.]'};
    }
    if (part && typeof part.text === 'string') return {...firewall.scrub(part), text:content(part.text)};
    return firewall.scrub(part);
  });
}
function compactPayloadImpl(input, options={}) {
  const budget=options.maxChars??BUDGET;
  if(!Number.isSafeInteger(budget)||budget<BUDGET||budget>MAX_INPUT)throw Error('context_policy_invalid');
  if (!input || !Array.isArray(input.messages) || input.messages.length > 4096 || Buffer.byteLength(JSON.stringify(input),'utf8') > MAX_INPUT) throw Error('tool_input_too_large');
  const data = JSON.parse(JSON.stringify(input));
  for (const key of Object.keys(data)) if (!['messages', 'tools', 'tool_choice'].includes(key)) data[key] = firewall.scrub(data[key]);
  let latestUser = -1;
  const pending = new Map();
  const ids = new Set();
  const groups = [];
  let group;
  data.messages.forEach((m, index) => {
    if (!['system','developer','user','assistant','tool'].includes(m.role)) throw Error('invalid_tool_history');
    if (m.role === 'user') latestUser = index;
    m.content = content(m.content);
    if (m.role === 'system' || m.role === 'developer') {
      groups.push({messages:[m], pinned:true, index});
      group = undefined;
      return;
    }
    if (!group || m.role === 'user') {
      group = {messages:[], pinned:false, index}; groups.push(group);
    }
    group.messages.push(m);
    if (m.role === 'assistant' && m.tool_calls) {
      if (pending.size || !Array.isArray(m.tool_calls)) throw Error('invalid_tool_history');
      for (const call of m.tool_calls) {
        if (!call || typeof call.id !== 'string' || !call.id || ids.has(call.id) || !call.function || typeof call.function.name !== 'string') throw Error('invalid_tool_history');
        let args;
        try { args = JSON.parse(call.function.arguments); } catch (_) { throw Error('invalid_tool_history'); }
        if (!args || typeof args !== 'object' || Array.isArray(args)) throw Error('invalid_tool_history');
        call.function.arguments = JSON.stringify(firewall.scrub(args));
        ids.add(call.id); pending.set(call.id, group);
        if (protectedTool.test(call.function.name)) group.pinned = true;
      }
    } else if (m.role === 'tool') {
      if (pending.get(m.tool_call_id) !== group) throw Error('invalid_tool_history');
      pending.delete(m.tool_call_id);
    } else if (pending.size) throw Error('invalid_tool_history');
  });
  // An outstanding call has no result; never ask the model to pretend it completed.
  if (pending.size) throw Error('invalid_tool_history');
  // With a larger verified profile retain full evidence while it fits.
  if(budget>BUDGET&&size(data)<=budget)return {data,compacted:JSON.stringify(data)!==JSON.stringify(input),notes:['data_firewall','context_retained']};
  if (latestUser < 0 && groups.length) groups.at(-1).pinned = true;
  const seen = new Set();
  let shortened = 0, duplicates = 0, dropped = 0;
  // Walk backwards so the most recent full copy remains available.
  for (const g of groups.slice().reverse()) {
    if (g.index >= latestUser && latestUser >= 0) g.pinned = true;
    for (const m of g.messages.slice().reverse()) {
      if (m.role !== 'tool' || typeof m.content !== 'string') continue;
      const hash = digest(m.content);
      if (!g.pinned && seen.has(hash) && m.content.length > 512) {
        m.content = '[Duplicate earlier tool output omitted; sha256=' + hash + '. Use the later full copy; this marker is not new execution evidence.]'; duplicates++;
      } else if (!g.pinned && m.content.length > 8000) { m.content = excerpt(m.content); shortened++; }
      seen.add(hash);
    }
  }
  const rebuild = () => { data.messages = groups.flatMap(g => g.messages); };
  rebuild();
  while (size(data) > budget) {
    const index = groups.findIndex(g => !g.pinned);
    if (index < 0) throw Error('tool_input_too_large');
    groups.splice(index, 1); dropped++; rebuild();
  }
  // Tool declarations and critical instructions remain intact. No schema clipping.
  return {data, compacted:JSON.stringify(data) !== JSON.stringify(input), notes:['data_firewall', 'groups_dropped='+dropped, 'tool_outputs_shortened='+shortened, 'duplicates_removed='+duplicates]};
}
function compactPayload(input,options={}){return firewall.withBudget(()=>compactPayloadImpl(input,options));}
module.exports = {compactPayload, BUDGET};

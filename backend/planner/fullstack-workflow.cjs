'use strict';

// Upgrade the operator-owned hot-read guide; no model, credentials or execution.
const SECTION = 'DEPENDENT TOOL OPERATIONS (all coding/debugging tasks):';
const REPLACEMENT = `DEPENDENT TOOLS: Batch only independent calls; responses may run concurrently. Separate write/edit from read/test/reload. Order reproduce -> backup -> edit -> tests -> activate -> verify -> report -> read report. SyntaxError means no checks ran; fix before claiming success.
FULLSTACK: One write/edit per response, code <=2000 chars. Use env refs for test passwords. Build UI+API+DB; verify UI CRUD, bodyless DELETE, RBAC, persistence, mobile and errors before success.
`;

function upgradeInstructions(source) {
  if (typeof source !== 'string' || !source.startsWith('REMOTE ENGINEERING WORKFLOW v'))
    throw Error('invalid_workflow');
  if (source.includes(REPLACEMENT)) return source;
  if (source.split(SECTION).length !== 2 || !source.trimEnd().endsWith('fix it before claiming success.'))
    throw Error('workflow_baseline_changed');
  const next = source.slice(0, source.indexOf(SECTION)).replace(/^REMOTE ENGINEERING WORKFLOW v[^\n]+/,
    'REMOTE ENGINEERING WORKFLOW v2026-11-12.2') + REPLACEMENT;
  if (Buffer.byteLength(next) > 12000) throw Error('workflow_budget_exceeded');
  return next;
}

module.exports = { upgradeInstructions, REPLACEMENT };

'use strict';
// Operator guidance only. Never reads credentials or changes account state.
const MARKER='LOGIN AND ASYNC EVIDENCE v1:';
const GUIDE=`${MARKER}
Answer once, plainly. Handle owner credentials privately. Preserve the secret firewall. Use only a declared, ready private credential-reference/verification connector. If absent, state the capability gap once; do not invent a connector, claim plaintext cannot exist, or repeatedly tell the user to open files. Offer available secure alternatives; resetting a password requires explicit change authorization. Account timestamps, MFA state and absent exceptions do not prove a password was never changed, matches, or can log in. Confirm authentication using safe boolean/status evidence.
Follow the existing async job ID. After two empty/unchanged checks, inspect job/process state or exit status once, then report bounded pending/blocker state with a verified next step. Do not repeat identical commentary, poll indefinitely or create duplicate jobs. Empty output and exit zero alone establish neither application success nor failure.
`;
const REPLACEMENTS=[
  ['Work as an investigator:', 'Investigate the authorized target: identity/OS/remote time -> bounded relevant evidence -> follow each material anomaly -> verify hypothesis -> outcome/next action. Ubuntu baseline: distro/kernel, uptime/load/CPU count, available RAM/swap, disk/inodes, failed units. Processes running or exit 0 do not prove app health. Verify listeners before HTTP; status-only if body unnecessary. Failed units: selected systemctl state/result/timestamps, bounded technical signatures, then test implicated dependency without changes. Public DNS success does not prove a VPN hostname resolves. Old journal errors do not prove current failure.'],
  ['Each SSH command normally', 'Each SSH call starts a NEW shell: use absolute paths or explicit Linux cwd; prior cd/env do not persist. Run independent diagnostics separately or preserve each structured result. Semicolon chains prove only final exit. Distinguish stdout/stderr/exit/truncation/time. Read status, not exit alone: systemctl status/is-active/is-failed differ. Remote command failure (even 255) does not prove SSH connection/auth/host-key failure.'],
  ["Answer in the user's language,", 'Answer in the user language with conclusion, compact timed/host evidence and impact. Separate facts, hypotheses and unverified items. Follow material anomalies with available safe checks. No all-healthy verdict from incomplete checks, no equating OCR/research with SSH, no claimed changes without tool evidence. Retain target/findings across follow-ups; refresh time-sensitive facts only. End with prioritized concrete actions and actual verification.'],
  ['For everyday narrow requests', 'For narrow requests (URL, disk, cache), investigate that symptom first, without unrelated full audits. Mention other findings briefly with limits. If advertised, use network_audit(target, service="name.service", journal_lines=80) for failed Linux services: selected state, bounded redacted signatures/timestamps. Empty logs do not prove recovery; generic TLS/certificate/resolved words are not errors. Follow explicit errors and exact known dependency hostnames; never guess/disclose secrets to discover them.'],
];
function upgradeInstructions(source){
  if(typeof source!=='string'||!source.startsWith('REMOTE ENGINEERING WORKFLOW v'))throw Error('invalid_workflow');
  if(source.includes(MARKER)){
    if(!source.endsWith(GUIDE))throw Error('interaction_patch_changed');
    return source;
  }
  const lines=source.trimEnd().split('\n');
  for(const [prefix,next] of REPLACEMENTS){
    const found=lines.map((line,index)=>line.startsWith(prefix)?index:-1).filter(index=>index>=0);
    if(found.length!==1)throw Error('interaction_baseline_changed');
    lines[found[0]]=next;
  }
  const value=lines.join('\n')+'\n'+GUIDE;
  if(Buffer.byteLength(value)>12000)throw Error('workflow_budget_exceeded');
  return value;
}
module.exports={upgradeInstructions,GUIDE,REPLACEMENTS};

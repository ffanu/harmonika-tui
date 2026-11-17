#!/usr/bin/env python3
"""Harmonika Interactive Engine: watch, log streaming, jobs, approvals, history, pager, action cards.

Actions:
  watch            — run any MCP tool repeatedly (interval), snapshot + diff vs previous run
  logs             — live stream logs for N seconds (journalctl -f) then analyze new lines
  jobs_register    — register background job (name, progress tracking)
  jobs_list        — all jobs table
  jobs_progress    — update job progress
  jobs_complete    — mark done/failed (+ toast)
  diff             — colored unified diff between old/new
  approval_request — dangerous op → pending approval with diff (TTL)
  approval_list    — pending approvals
  approval_decide  — approve (executes stored action) or reject
  history_add      — record change (install/config/approval) for timeline
  history_list     — rollback timeline
  pager            — paginate + search large results
  suggest          — action cards [RUN]/[DRY-RUN]/[CANCEL] for chat
"""
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

BASE = os.environ.get('HARMONIKA_GLOBAL_CONFIG') or os.environ.get('HARMONIKA_CONFIG_DIR') or os.path.expanduser('~/.config/harmonika')
IState = os.path.join(BASE, 'interactive')
JOBS_FILE = os.path.join(IState, 'jobs.json')
APPROVALS_FILE = os.path.join(IState, 'approvals.json')
HISTORY_FILE = os.path.join(IState, 'history.json')
WATCH_DIR = os.path.join(IState, 'watch')
_WATCH_PREVIOUS = {}

GREEN = '\x1b[32m'; RED = '\x1b[31m'; YELLOW = '\x1b[33m'; CYAN = '\x1b[36m'
MAGENTA = '\x1b[35m'; GRAY = '\x1b[90m'; BOLD = '\x1b[1m'; RESET = '\x1b[0m'


def _read(f, default):
    try:
        return json.load(open(f))
    except Exception:
        return default


def _write(f, data):
    os.makedirs(os.path.dirname(f), mode=0o700, exist_ok=True)
    temporary=f+'.tmp-'+uuid.uuid4().hex
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(data,stream,indent=2)
        os.replace(temporary,f)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def _get_handlers():
    running=sys.modules.get('__main__')
    if running is not None and hasattr(running,'HANDLERS'):return running.HANDLERS
    import mcp_server
    return mcp_server.HANDLERS


def _toast(title, msg=''):
    return f'\x1b]9;{title}{" — " + msg if msg else ""}\x07'


# ═══════════ WATCH MODE ═══════════

def watch(args):
    """Run tool on interval; returns current + diff vs previous snapshot."""
    tool = args.get('tool', '')
    interval = max(2, int(args.get('interval', 5)))
    iterations = min(int(args.get('iterations', 1)), 200)
    tool_args = args.get('arguments', {})
    if not tool:
        return {'ok': False, 'error': 'tool required'}
    handlers = _get_handlers()
    if tool=='interactive':return {'ok':False,'error':'recursive_watch_refused'}
    if tool not in handlers:
        return {'ok': False, 'error': f'unknown tool: {tool}'}
    signature=hashlib.sha256(json.dumps([tool,tool_args],sort_keys=True).encode()).hexdigest()
    prev = _WATCH_PREVIOUS.get(signature)
    results = []
    for i in range(iterations):
        try:
            r = handlers[tool](tool_args)
        except Exception as e:
            r = {'ok':False,'error':'watch_tool_failed'}
        results.append(r)
        if i < iterations - 1:
            time.sleep(interval)
    cur = results[-1]
    if len(_WATCH_PREVIOUS)>=64:_WATCH_PREVIOUS.pop(next(iter(_WATCH_PREVIOUS)))
    _WATCH_PREVIOUS[signature]=_jsonable(cur)
    d = None
    if prev is not None:
        d = _smart_diff(prev, cur)
    changed = bool(d and d.get('changed'))
    return {'ok': True, 'action': 'watch', 'tool': tool,
            'interval': interval, 'iterations': iterations,
            'result': _jsonable(cur), 'changed': changed,
            'diff': d,
            'toast': _toast(f'{tool} changed') if changed else '',
            'next': f'Call again to compare next run. iterations>1 for multi-cycle.'}


def _jsonable(o):
    try:
        json.dumps(o)
        return o
    except Exception:
        return str(o)


def _smart_diff(a, b):
    sa = json.dumps(a, indent=1, sort_keys=True, default=str).split('\n')
    sb = json.dumps(b, indent=1, sort_keys=True, default=str).split('\n')
    diff = [l for l in difflib.unified_diff(sa, sb, lineterm='', n=1)]
    changed = any(l.startswith(('+', '-')) and not l.startswith(('+++', '---')) for l in diff)
    return {'changed': changed, 'lines': [l[:200] for l in diff[:40]]}


# ═══════════ LOG STREAMING ═══════════

def logs(args):
    """Stream logs live for duration seconds, return new lines + analysis."""
    target = args.get('target', 'local')
    service = args.get('service', '')
    query = args.get('query', '')
    duration = min(int(args.get('duration', 10)), 120)
    tail = int(args.get('tail', 20))
    base_cmd = (f'journalctl -u {service} ' if service else 'journalctl ') + '-n 0 -f --no-pager'
    if query:
        base_cmd += f' | grep -i --line-buffered "{query}"'
    if target == 'local':
        try:
            p = subprocess.run(f'timeout {duration} {base_cmd} 2>/dev/null || (gtimeout {duration} {base_cmd} 2>/dev/null) || true',
                               shell=True, capture_output=True, text=True, timeout=duration + 10)
            out = p.stdout
        except Exception as e:
            return {'ok': False, 'error': f'stream failed: {e} (macOS local: use target=linux-server)'}
    else:
        from ops_tools import _ssh_exec
        cmd = f'timeout {duration} {base_cmd} 2>/dev/null || true'
        r = _ssh_exec(target, cmd, timeout=duration + 15)
        if not r.get('ok'):
            return {'ok': False, 'error': r.get('error', 'ssh failed')}
        out = r.get('output', '')
    lines = [l for l in out.split('\n') if l.strip()]
    err_pat = re.compile(r'\b(ERROR|FATAL|CRITICAL|PANIC|failed|OOM)\b', re.I)
    warn_pat = re.compile(r'\b(WARN|RETRY|DEPRECATED)\b', re.I)
    errs = [l for l in lines if err_pat.search(l)]
    warns = [l for l in lines if warn_pat.search(l)]
    return {'ok': True, 'action': 'logs', 'target': target, 'service': service or 'system',
            'duration_s': duration, 'new_lines': len(lines),
            'errors': len(errs), 'warnings': len(warns),
            'error_lines': errs[:10], 'warning_lines': warns[:5],
            'recent': lines[-tail:],
            'visual_bar': _log_bar(lines, errs, warns)}


def _log_bar(lines, errs, warns):
    from viz_tools import gauge, sparkline
    n = len(lines)
    err_pct = (len(errs) / n * 100) if n else 0
    return [gauge(err_pct, f'errors {len(errs)}/{n}', 26), f'streamed {n} lines']


# ═══════════ JOB QUEUE ═══════════

def jobs_register(args):
    jobs = _read(JOBS_FILE, [])
    jid = uuid.uuid4().hex[:8]
    job = {'id': jid, 'name': args.get('name', 'job'), 'tool': args.get('tool', ''),
           'status': 'running', 'progress': 0, 'message': '',
           'started': datetime.now().isoformat(), 'ended': None}
    jobs.append(job)
    _write(JOBS_FILE, jobs)
    return {'ok': True, 'job': job, 'hint': f'jobs_progress id={jid} — jobs_complete when done'}


def jobs_list(args):
    jobs = _read(JOBS_FILE, [])
    show = args.get('status', 'all')
    if show != 'all':
        jobs = [j for j in jobs if j.get('status') == show]
    jobs = jobs[-int(args.get('limit', 30)):]
    from viz_tools import table, progress
    rows = [[j['id'], j['name'], j['status'], f"{j.get('progress', 0)}%",
             j.get('message', '')[:30], j['started'][11:19]] for j in jobs]
    colors = [GREEN if j['status'] == 'done' else (RED if j['status'] == 'failed' else YELLOW) for j in jobs]
    t = table(['ID', 'Name', 'Status', 'Prog', 'Msg', 'Started'], rows, colors)
    running = [j for j in jobs if j['status'] == 'running']
    bars = [progress(j['progress'], 100, j['name']) for j in running[:5]]
    return {'ok': True, 'jobs': jobs, 'table': t, 'running_bars': bars,
            'counts': {'running': len(running), 'done': len([j for j in jobs if j['status'] == 'done']),
                       'failed': len([j for j in jobs if j['status'] == 'failed'])}}


def jobs_progress(args):
    jid = args.get('id', '')
    pct = max(0, min(100, int(args.get('progress', 0))))
    jobs = _read(JOBS_FILE, [])
    for j in jobs:
        if j['id'] == jid:
            j['progress'] = pct
            if args.get('message'):
                j['message'] = str(args['message'])[:100]
            _write(JOBS_FILE, jobs)
            from viz_tools import progress as pbar
            return {'ok': True, 'job': j, 'bar': pbar(pct, 100, j['name'])}
    return {'ok': False, 'error': f'job not found: {jid}'}


def jobs_complete(args):
    jid = args.get('id', '')
    status = args.get('status', 'done') if args.get('status') in ('done', 'failed', 'cancelled') else 'done'
    jobs = _read(JOBS_FILE, [])
    for j in jobs:
        if j['id'] == jid:
            j['status'] = status
            j['progress'] = 100 if status == 'done' else j.get('progress', 0)
            j['ended'] = datetime.now().isoformat()
            if args.get('message'):
                j['message'] = str(args['message'])[:100]
            _write(JOBS_FILE, jobs)
            history_add({'type': 'job', 'target': j['name'], 'summary': f'{status}: {j.get("message","")}',
                         'ref': jid})
            return {'ok': True, 'job': j, 'toast': _toast(f'Job {status}: {j["name"]}',
                     args.get('message', '')) if status == 'done' else ''}
    return {'ok': False, 'error': f'job not found: {jid}'}


# ═══════════ DIFF & APPROVALS ═══════════

def diff(args):
    old = str(args.get('old', ''))
    new = str(args.get('new', ''))
    name = args.get('name', 'change')
    d = [l for l in difflib.unified_diff(old.split('\n'), new.split('\n'),
                                         fromfile=f'{name} (current)', tofile=f'{name} (new)', lineterm='')]
    colored = []
    for l in d:
        if l.startswith('+'):
            colored.append(GREEN + l + RESET)
        elif l.startswith('-'):
            colored.append(RED + l + RESET)
        elif l.startswith('@@'):
            colored.append(CYAN + l + RESET)
        else:
            colored.append(l)
    added = len([l for l in d if l.startswith('+') and not l.startswith('+++')])
    removed = len([l for l in d if l.startswith('-') and not l.startswith('---')])
    return {'ok': True, 'added': added, 'removed': removed,
            'diff': '\n'.join(colored[:80]), 'plain': '\n'.join(d[:80])}


def approval_request(args):
    aps = _read(APPROVALS_FILE, [])
    aid = uuid.uuid4().hex[:8]
    ap = {'id': aid, 'title': args.get('title', 'approval'), 'risk': args.get('risk', 'medium'),
          'diff': args.get('diff', ''), 'execute_tool': args.get('execute_tool', ''),
          'execute_args': args.get('execute_args', {}),
          'created': time.time(), 'ttl': int(args.get('ttl', 600)),
          'status': 'pending'}
    aps.append(ap)
    _write(APPROVALS_FILE, aps)
    return {'ok': True, 'approval_id': aid, 'approval': ap,
            'card': _card(ap),
            'next': f'approval_decide id={aid} decision=approve|reject'}


def _card(ap):
    risk_c = RED if ap['risk'] == 'high' else YELLOW
    return (f'{risk_c}{BOLD}⚠ APPROVAL NEEDED{RESET} {BOLD}{ap["title"]}{RESET}\n'
            f'{ap["diff"]}\n'
            f'{GRAY}risk={ap["risk"]} expires in {ap["ttl"]}s id={ap["id"]}{RESET}\n'
            f'[{GREEN}APPROVE{RESET}] [{RED}REJECT{RESET}] → approval_decide id={ap["id"]} decision=...')


def approval_list(args):
    now = time.time()
    aps = _read(APPROVALS_FILE, [])
    live = [a for a in aps if a['status'] == 'pending' and now - a['created'] < a['ttl']]
    for a in aps:
        if a['status'] == 'pending' and now - a['created'] >= a['ttl']:
            a['status'] = 'expired'
    _write(APPROVALS_FILE, aps)
    return {'ok': True, 'pending': live, 'cards': [_card(a) for a in live]}


def approval_decide(args):
    aid = args.get('id', '')
    decision = args.get('decision', '')
    if decision not in ('approve', 'reject'):
        return {'ok': False, 'error': 'decision must be approve|reject'}
    aps = _read(APPROVALS_FILE, [])
    ap = next((a for a in aps if a['id'] == aid), None)
    if not ap or ap['status'] != 'pending':
        return {'ok': False, 'error': 'approval not found/expired'}
    if time.time() - ap['created'] > ap['ttl']:
        ap['status'] = 'expired'
        _write(APPROVALS_FILE, aps)
        return {'ok': False, 'error': 'approval expired'}
    if decision == 'reject':
        ap['status'] = 'rejected'
        _write(APPROVALS_FILE, aps)
        history_add({'type': 'approval', 'target': ap['title'], 'summary': 'rejected', 'ref': aid})
        return {'ok': True, 'decision': 'rejected'}
    ap['status'] = 'approved'
    _write(APPROVALS_FILE, aps)
    history_add({'type': 'approval', 'target': ap['title'], 'summary': 'approved → executing', 'ref': aid})
    if ap.get('execute_tool'):
        handlers = _get_handlers()
        h = handlers.get(ap['execute_tool'])
        if not h:
            return {'ok': False, 'error': f'execute_tool missing: {ap["execute_tool"]}'}
        try:
            r = h(ap.get('execute_args', {}))
        except Exception as e:
            return {'ok': False, 'error': 'approved_execution_failed'}
        return {'ok': True, 'decision': 'approved', 'executed': ap['execute_tool'], 'result': _jsonable(r)}
    return {'ok': True, 'decision': 'approved', 'note': 'no execute_tool attached'}


# ═══════════ HISTORY / TIMELINE ═══════════

def history_add(args):
    h = _read(HISTORY_FILE, [])
    entry = {'ts': datetime.now().isoformat(), 'type': args.get('type', 'change'),
             'target': args.get('target', ''), 'summary': args.get('summary', ''),
             'ref': args.get('ref', ''), 'rollback_hint': args.get('rollback_hint', '')}
    h.append(entry)
    _write(HISTORY_FILE, h[-500:])
    return {'ok': True, 'entry': entry}


def history_list(args):
    h = _read(HISTORY_FILE, [])
    limit = int(args.get('limit', 25))
    filt = args.get('type', 'all')
    if filt != 'all':
        h = [e for e in h if e['type'] == filt]
    h = h[-limit:][::-1]
    from viz_tools import table
    rows = [[e['ts'][11:19], e['type'], e['target'][:20], e['summary'][:40], e.get('rollback_hint', '')[:35]] for e in h]
    t = table(['Time', 'Type', 'Target', 'Summary', 'Rollback'], rows)
    return {'ok': True, 'timeline': h, 'table': t,
            'hint': 'Rollback: for install → safe_install action=rollback; for configs → config_backup + network_config dry_run first'}


# ═══════════ PAGER ═══════════

def pager(args):
    text = args.get('text', '')
    page = max(1, int(args.get('page', 1)))
    size = min(int(args.get('page_size', 40)), 200)
    search = args.get('search', '').lower()
    lines = text.split('\n')
    if search:
        hits = [{'line_no': i + 1, 'text': l[:200]} for i, l in enumerate(lines) if search in l.lower()]
        return {'ok': True, 'total_lines': len(lines), 'search': search,
                'matches': len(hits), 'results': hits[:30]}
    total_pages = max(1, (len(lines) + size - 1) // size)
    page = min(page, total_pages)
    chunk = lines[(page - 1) * size:page * size]
    return {'ok': True, 'total_lines': len(lines), 'page': page, 'total_pages': total_pages,
            'lines': chunk, 'nav': f'page {page}/{total_pages} — next: page={page + 1}'}


# ═══════════ SUGGEST (action cards) ═══════════

def suggest(args):
    title = args.get('title', 'Actions')
    actions = args.get('actions', [])
    lines = [f'{BOLD}{CYAN}⚡ {title}{RESET}']
    for a in actions[:6]:
        lbl = a.get('label', 'run')
        tool = a.get('tool', '')
        a_args = a.get('arguments', {})
        style = a.get('style', 'primary')
        c = {'primary': GREEN, 'danger': RED, 'warn': YELLOW, 'info': CYAN}.get(style, GREEN)
        arg_str = json.dumps(a_args, separators=(',', ':'))[:120] if a_args else '{}'
        lines.append(f'  [{c}{BOLD}{lbl.upper()}{RESET}] {GRAY}{tool}{RESET} {GRAY}{arg_str}{RESET}')
    lines.append(f'{GRAY}→ bilang AI: jalankan [LABEL], atau tulis manual di atas.{RESET}')
    return {'ok': True, 'card': '\n'.join(lines)}


# ═══════════ DISPATCH ═══════════

def _dispatch(args):
    a = args.get('action', '')
    if os.name=='nt' and a=='logs' and args.get('target','local') in ('local','localhost','',None):return {'ok':False,'error':'unsupported_local_platform','platform':'windows','operation':'logs','executed':False}
    fns = {'watch': watch, 'logs': logs,
           'jobs_register': jobs_register, 'jobs_list': jobs_list,
           'jobs_progress': jobs_progress, 'jobs_complete': jobs_complete,
           'diff': diff,
           'approval_request': approval_request, 'approval_list': approval_list,
           'approval_decide': approval_decide,
           'history_add': history_add, 'history_list': history_list,
           'pager': pager, 'suggest': suggest}
    if a in fns:
        try:
            return fns[a](args)
        except Exception as e:
            return {'ok': False, 'error': 'interactive_operation_failed'}
    return {'ok': False, 'error': f'unknown action: {a}. Use: {", ".join(fns)}'}


INTERACTIVE_TOOLS = {
    'interactive': {
        'description': 'Interactive engine: watch (run any tool on interval + diff), logs (live journalctl -f streaming), '
                       'jobs_* (background job queue with progress bars + toast notifications), diff (colored), '
                       'approval_* (pending approvals with diff, TTL, approve→auto-execute), '
                       'history_* (rollback timeline of all changes), pager (paginate/search results), '
                       'suggest (action cards [RUN]/[DRY-RUN] for chat).',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string',
                           'enum': ['watch', 'logs', 'jobs_register', 'jobs_list', 'jobs_progress',
                                    'jobs_complete', 'diff', 'approval_request', 'approval_list',
                                    'approval_decide', 'history_add', 'history_list', 'pager', 'suggest']},
                'tool': {'type': 'string', 'description': 'MCP tool name (watch)'},
                'arguments': {'type': 'object'},
                'interval': {'type': 'number'}, 'iterations': {'type': 'number'},
                'target': {'type': 'string'}, 'service': {'type': 'string'},
                'duration': {'type': 'number', 'description': 'seconds to stream (logs)'},
                'name': {'type': 'string'}, 'id': {'type': 'string'},
                'progress': {'type': 'number'}, 'status': {'type': 'string'},
                'message': {'type': 'string'},
                'old': {'type': 'string'}, 'new': {'type': 'string'},
                'title': {'type': 'string'}, 'risk': {'type': 'string'},
                'decision': {'type': 'string', 'enum': ['approve', 'reject']},
                'execute_tool': {'type': 'string'}, 'execute_args': {'type': 'object'},
                'text': {'type': 'string'}, 'page': {'type': 'number'},
                'page_size': {'type': 'number'}, 'search': {'type': 'string'},
                'actions': {'type': 'array', 'items': {'type': 'object'}},
            },
            'required': ['action'],
        },
    },
}

INTERACTIVE_HANDLERS = {'interactive': _dispatch}

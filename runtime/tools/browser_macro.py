"""Typed browser macros. Linux coordinates opaque hashes; page data stays local."""
import hashlib
import json
import socket
import time
from desktop_browser import BrowserError
from urllib.parse import urlparse

VERSION = '1.0.1'
ACTIONS = {'navigate', 'type', 'select', 'set_checked', 'click', 'reveal', 'read'}


def origin(url):
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('invalid_macro_url')
    return p.scheme + '://' + p.netloc


def compile_plan(args):
    if type(args) is not dict or set(args) - {'action', 'tab_id', 'start_url', 'steps', 'dry_run'}:
        raise ValueError('invalid_macro_request')
    tab = args.get('tab_id')
    if not isinstance(tab, str) or not tab.isdigit():
        raise ValueError('invalid_tab_id')
    from desktop_browser import safe_url
    start = safe_url(args.get('start_url', ''))
    scope = origin(start)
    steps = args.get('steps')
    if type(steps) is not list or not 1 <= len(steps) <= 12 or type(args.get('dry_run', True)) is not bool:
        raise ValueError('invalid_macro_steps')
    if len(json.dumps(args, ensure_ascii=True)) > 24000:
        raise ValueError('macro_size_limit')
    commands, bindings = [], {}
    for step in steps:
        if type(step) is not dict:
            raise ValueError('invalid_macro_step')
        action = step.get('action')
        fields = {'navigate': {'url'}, 'type': {'label', 'text'}, 'select': {'label', 'value'},
                  'set_checked': {'label', 'checked'}, 'click': {'label'}, 'reveal': {'label'}, 'read': set()}
        if action not in ACTIONS or set(step) - ({'action', 'frame', 'expected_text', 'expected_rows'} | fields[action]):
            raise ValueError('invalid_macro_step')
        if not fields[action] <= set(step):
            raise ValueError('missing_macro_parameter')
        expected = step.get('expected_text')
        if not isinstance(expected, str) or not expected.strip() or len(expected) > 500:
            raise ValueError('explicit_postcondition_required')
        for key in ('label', 'text', 'value', 'frame'):
            if key in step and (not isinstance(step[key], str) or not step[key] or len(step[key]) > (2048 if key == 'text' else 180)):
                raise ValueError('invalid_macro_parameter')
        if 'frame' in step:
            import re
            if not re.fullmatch(r'[A-Za-z][\w-]{0,79}', step['frame']):
                raise ValueError('invalid_frame_id')
        if action == 'navigate' and (step.get('frame') or origin(safe_url(step['url'])) != scope):
            raise ValueError('macro_origin_boundary')
        if action == 'set_checked' and type(step['checked']) is not bool:
            raise ValueError('invalid_checkbox_state')
        if 'expected_rows' in step:
            rows = step['expected_rows']
            if type(rows) is not list or len(rows) > 80 or any(type(r) is not list or len(r) > 16 or any(type(c) is not str or len(c) > 200 for c in r) for r in rows):
                raise ValueError('invalid_expected_rows')
        digest = hashlib.sha256(json.dumps(step, sort_keys=True).encode()).hexdigest()
        # Include index so intentional identical reads remain distinct protocol phases.
        identity = str(len(commands)) + ':' + digest
        bindings[identity] = step
        commands.append({phase: json.dumps({'op': op, 'step': identity}, sort_keys=True)
                         for phase, op in [('command', 'browser_step'), ('verify', 'browser_check')]})
    return {'target': 'browser-tab-' + tab, 'dry_run': args.get('dry_run', True), 'config': commands}, bindings


def invoke(args, desktop, exec_dom, transport=None):
    try:
        plan, bindings = compile_plan(args)
    except (ValueError, BrowserError) as exc:
        return {'ok': False, 'state': 'rejected', 'error': str(exc), 'actions_dispatched': 0}
    current_url = args['start_url']
    receipts, dispatched = [], 0
    last_error = None

    def normalized(value):
        return ' '.join(value.split())

    def callback(command, timeout):
        nonlocal current_url, dispatched, last_error
        item = json.loads(command)
        step = bindings[item['step']]
        tab, frame = args['tab_id'], step.get('frame', '')
        began = time.monotonic()
        action_started = False

        def scoped():
            page = exec_dom(tab, 'JSON.stringify({ok:true,url:location.href})')
            if not page.get('ok') or page.get('url') != current_url:
                raise ValueError('macro_scope_changed')

        def snapshot():
            scoped()
            page = desktop({'action': 'snapshot', 'tab_id': tab, 'frame': frame})
            if not page.get('ok'):
                raise ValueError(page.get('error', 'observation_failed'))
            return page

        def match(page):
            items = [c for c in page.get('controls', []) if c.get('label', '').strip() == step.get('label')]
            if len(items) > 1:
                raise ValueError('ambiguous_control')
            return items[0] if items else None

        try:
            scoped()
            if item['op'] == 'browser_step':
                action = step['action']
                if action in ('read', 'reveal', 'navigate'):
                    call = {'action': action, 'tab_id': tab, 'frame': frame}
                    if action == 'read':
                        snapshot()
                    else:
                        call.update({k: step[k] for k in ('url', 'label') if k in step})
                        action_started = True
                        response = desktop(call)
                        if response.get('action_sent'): dispatched += 1
                        if not response.get('ok'): raise ValueError(response.get('error', 'action_failed'))
                        if action == 'navigate': current_url = step['url']
                else:
                    while time.monotonic() - began < min(16, timeout - 2):
                        page = snapshot()
                        control = match(page)
                        if control and not control.get('disabled') and page.get('ready'): break
                        time.sleep(.15)
                    else:
                        raise ValueError('control_not_ready')
                    if control.get('href'):
                        raise ValueError('use_explicit_navigate')
                    if action=='click' and normalized(step['expected_text']) in normalized(page.get('main_text','')):
                        tables=page.get('tables',[])
                        if 'expected_rows' not in step or (tables and tables[0].get('rows')==step['expected_rows']):
                            raise ValueError('postcondition_already_satisfied_choose_new_result')
                    call = {'action': action, 'tab_id': tab, 'frame': frame, 'view_id': page['view_id'], 'ref': control['ref']}
                    call.update({k: step[k] for k in ('text', 'value', 'checked') if k in step})
                    action_started = True
                    response = desktop(call)
                    if response.get('action_sent'): dispatched += 1
                    if not response.get('ok'): raise ValueError(response.get('error', 'action_failed'))
            else:
                while time.monotonic() - began < min(18, timeout - 2):
                    page = snapshot()
                    passed = page.get('ready') and normalized(step['expected_text']) in normalized(page.get('main_text', ''))
                    if 'expected_rows' in step:
                        tables = page.get('tables', [])
                        passed = passed and bool(tables) and not tables[0].get('truncated') and tables[0]['rows'] == step['expected_rows']
                    if step['action']=='reveal':
                        passed = passed and bool(match(page))
                    if step['action'] in ('select', 'set_checked', 'type'):
                        control = match(page)
                        field, expected = ('checked', step['checked']) if step['action'] == 'set_checked' else ('value', step.get('value', step.get('text')))
                        passed = passed and bool(control) and control.get(field) == expected
                    if passed:
                        receipts.append({'step': int(item['step'].split(':')[0]), 'verified': True,
                                         'frame': frame, 'text': page.get('main_text', '')[:2000],
                                         'tables': page.get('tables', [])[:2]})
                        break
                    time.sleep(.15)
                else:
                    raise ValueError('postcondition_timeout')
            return {'ok': True, 'exit_status': 0, 'execution_state': 'completed', 'connection_status': 'connected', 'output': 'postcondition_checked' if item['op'] == 'browser_check' else 'step_observed'}
        except Exception as exc:
            # Only technical signatures; page contents/errors never sent to Linux.
            allowed = {'macro_scope_changed', 'ambiguous_control', 'control_not_ready', 'use_explicit_navigate',
                       'postcondition_timeout', 'postcondition_already_satisfied_choose_new_result', 'cross_origin_frame_manual_only', 'frame_not_found',
                       'sensitive_action_manual_only', 'sensitive_input_manual_only', 'only_filter_controls_supported',
                       'stale_control_take_snapshot', 'control_disabled', 'only_search_fields_supported'}
            last_error = str(exc) if str(exc) in allowed else 'browser_execution_uncertain'
            unknown = last_error == 'browser_execution_uncertain' and action_started
            return {'ok': False, 'exit_status': None if unknown else 1,
                    'execution_state': 'unknown' if unknown else 'completed', 'connection_status': 'connected',
                    'output': last_error}

    if transport is None:
        from config_transaction_client import exchange
        transport = exchange
    result = transport(plan, {'host': socket.gethostname() + ':' + origin(args['start_url']) + ':tab:' + args['tab_id']}, callback,
                       worker_command=['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', 'vps',
                                       'python3 -I /opt/harmonika-ai/browser-macro/worker.py'])
    result.update(engine='harmonika-browser-macro', engine_version=VERSION,
                  actions_dispatched=dispatched, receipts=receipts, content_is_untrusted=True,
                  scope={'tab_id': args['tab_id'], 'start_url': args['start_url']}, automatic_action_retry=False)
    result.pop('changes_applied', None)  # Browser actions are not SSH configuration changes.
    if last_error: result['error'] = last_error
    return result

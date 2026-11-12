"""Pure configuration coordinator. Credentials and execution stay in the adapter.

No commands, output or user data are persisted by this module. A verified rollback
is a compensation of explicitly supplied steps, not a general atomic SSH promise.
"""
import hashlib
import json
import time

VERSION = '1.0.0'
MAX_STEPS = 16
MAX_BYTES = 32768


class PlanError(ValueError):
    pass


def text(value):
    if type(value) is not str or not value.strip() or len(value) > 8192 or '\x00' in value:
        raise PlanError('invalid_config_command')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise PlanError('invalid_config_command') from None
    return value.strip()


def legacy_commands(value):
    """Split only simple top-level separators, retaining original shell quoting.

    Compound scripts/substitution/heredocs must use an explicit command array;
    silently guessing their grammar could turn comment text into a command.
    """
    value = text(value)
    result, start, quote, escaped = [], 0, None, False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == '\\' and quote != "'":
            escaped = True
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if char == '`' or (char == '$' and value[index:index + 2] == '$('):
            raise PlanError('structured_commands_required')
        if quote == '"':
            if char == '"':
                quote = None
            continue
        if char in ("'", '"'):
            if index and value[index - 1] == '$':
                raise PlanError('structured_commands_required')
            quote = char
        elif char in '#&|<>(){}[]':
            raise PlanError('structured_commands_required')
        elif char in ';\n':
            part = value[start:index].strip()
            if not part:
                if char == '\n':
                    start = index + 1
                    continue
                raise PlanError('empty_config_command')
            result.append(part)
            start = index + 1
    if quote or escaped:
        raise PlanError('invalid_config_quoting')
    if value[start:].strip():
        result.append(value[start:].strip())
    return result


def prepare(args):
    if type(args) is not dict or set(args) - {'target', 'config', 'dry_run'}:
        raise PlanError('invalid_config_request')
    if type(args.get('target')) is not str or not 1 <= len(args['target']) <= 100:
        raise PlanError('invalid_config_target')
    if type(args.get('dry_run', True)) is not bool:
        raise PlanError('invalid_config_dry_run')
    try:
        raw = json.dumps(args, ensure_ascii=True, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise PlanError('invalid_config_request') from None
    if len(raw) > MAX_BYTES:
        raise PlanError('config_size_limit')
    items = args.get('config')
    if type(items) is str:
        items = legacy_commands(items)
    if type(items) is not list or not 1 <= len(items) <= MAX_STEPS:
        raise PlanError('invalid_config_steps')
    steps = []
    for item in items:
        item = {'command': item} if type(item) is str else item
        if type(item) is not dict or 'command' not in item or set(item) - {
                'command', 'verify', 'rollback', 'rollback_verify'}:
            raise PlanError('invalid_config_step')
        steps.append({key: text(value) for key, value in item.items()})
    compensation = any('rollback' in step or 'rollback_verify' in step for step in steps)
    if compensation and not all('rollback' in step and 'rollback_verify' in step for step in steps):
        raise PlanError('complete_rollback_plan_required')
    plan = {'target': args['target'], 'steps': steps, 'dry_run': args.get('dry_run', True)}
    plan['plan_sha256'] = hashlib.sha256(json.dumps(
        {'target': plan['target'], 'steps': steps}, sort_keys=True).encode()).hexdigest()
    return plan


def run(args, executor, budget=75):
    """executor(command, timeout, index, phase) returns a structured SSH result."""
    try:
        plan = prepare(args)
    except PlanError as exc:
        return {'ok': False, 'error': str(exc), 'state': 'rejected',
                'changes_applied': False, 'safe_to_retry': False}
    if plan['dry_run']:
        return {'ok': True, **plan, 'commands': [s['command'] for s in plan['steps']],
                'state': 'preview', 'changes_applied': False, 'verified': False,
                'execution_performed': False}
    if executor is None:
        return {'ok': False, 'error': 'executor_not_configured', 'state': 'rejected',
                'changes_applied': False, 'safe_to_retry': False}
    deadline = time.monotonic() + budget
    results, affected = [], []
    failure = None

    def call(index, phase):
        command = plan['steps'][index][phase]
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            value = {'ok': False, 'error': 'transaction_deadline', 'execution_state': 'not_started',
                     'connection_status': 'not_attempted', 'exit_status': None}
        else:
            try:
                value = executor(command, min(30, remaining), index, phase)
                if type(value) is not dict:
                    raise TypeError()
            except Exception:
                value = {'ok': False, 'error': 'executor_failed', 'execution_state': 'unknown',
                         'connection_status': 'unknown', 'exit_status': None}
        completed = (value.get('execution_state') == 'completed'
                     and type(value.get('exit_status')) is int and value['exit_status'] >= 0)
        passed = (completed and value['exit_status'] == 0 and value.get('ok') is True
                  and value.get('connection_status') == 'connected')
        row = {'index': index, 'phase': phase, 'ok': passed,
               'execution_state': value.get('execution_state', 'unknown'),
               'connection_status': value.get('connection_status', 'unknown'),
               'exit_status': value.get('exit_status'),
               'command_sha256': hashlib.sha256(command.encode()).hexdigest(),
               'output': str(value.get('output', ''))[:1000],
               'stderr': str(value.get('stderr', ''))[:1000],
               'output_truncated': bool(value.get('output_truncated')) or len(str(value.get('output', ''))) > 1000,
               'stderr_truncated': bool(value.get('stderr_truncated')) or len(str(value.get('stderr', ''))) > 1000,
               'error': value.get('error') if value.get('error') in {
                   'ssh_command_failed', 'ssh_command_timeout', 'ssh_connect_timeout', 'ssh_auth_failed',
                   'ssh_hostkey_failed', 'ssh_transport_failed', 'transaction_deadline', 'executor_failed'} else None}
        results.append(row)
        if phase in ('verify', 'rollback_verify') and (row['output_truncated'] or row['stderr_truncated']):
            row['ok'] = False
            row['error'] = 'verification_output_incomplete'
        return row, completed

    for index, step in enumerate(plan['steps']):
        row, completed = call(index, 'command')
        if completed:
            affected.append(index)  # Even a failed command can have partial side effects.
        if not row['ok']:
            failure = row
            break
        if 'verify' in step:
            row, completed = call(index, 'verify')
            if not row['ok'] or row['output_truncated'] or row['stderr_truncated']:
                failure = row
                break
    base = {'plan_sha256': plan['plan_sha256'], 'target': plan['target'],
            'results': results, 'safe_to_retry': False, 'content_is_untrusted': True,
            'execution_performed': bool(results), 'attempted': sum(r['phase'] == 'command' for r in results)}
    if failure is None:
        verified = all('verify' in step for step in plan['steps'])
        return {**base, 'ok': True, 'state': 'verified' if verified else 'applied_unverified',
                'applied': len(affected), 'complete': True, 'changes_applied': True, 'verified': verified}
    # Unknown command/transport state must be reconciled before any compensation.
    if failure['execution_state'] != 'completed' or failure['connection_status'] != 'connected':
        return {**base, 'ok': False, 'state': 'needs_reconciliation', 'complete': False,
                'applied': len(affected), 'changes_applied': bool(affected) or failure['execution_state'] == 'unknown',
                'verified': False, 'rollback_attempted': False}
    if not all('rollback' in plan['steps'][index] for index in affected):
        return {**base, 'ok': False, 'state': 'failed_partial', 'complete': False,
                'applied': len(affected), 'changes_applied': bool(affected), 'verified': False,
                'rollback_attempted': False}
    for index in reversed(affected):
        row, _ = call(index, 'rollback')
        if not row['ok']:
            break
        row, _ = call(index, 'rollback_verify')
        if not row['ok'] or row['output_truncated'] or row['stderr_truncated']:
            break
    else:
        return {**base, 'ok': False, 'state': 'rolled_back', 'complete': False,
                'applied': 0, 'changes_applied': False, 'verified': False,
                'rollback_attempted': True, 'rollback_verified': True}
    return {**base, 'ok': False, 'state': 'rollback_failed', 'complete': False,
            'applied': len(affected), 'changes_applied': True, 'verified': False,
            'rollback_attempted': True, 'rollback_verified': False}

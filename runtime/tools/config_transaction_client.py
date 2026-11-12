"""Bounded private transport for the Linux config coordinator; no credentials in RPC."""
import hashlib
import json
import select
import subprocess
import time
from config_transaction_engine import prepare, PlanError


def invoke(args, load_targets, ssh_exec):
    """Bind the generic coordinator to the caller's existing private connector."""
    if not isinstance(args, dict):
        return {'ok': False, 'error': 'invalid_config_request', 'state': 'rejected', 'changes_applied': False}
    target = args.get('target')
    targets = load_targets()
    if not isinstance(target, str) or not isinstance(targets, dict) or target not in targets:
        return {'ok': False, 'error': 'target_not_found', 'state': 'rejected', 'changes_applied': False}
    info = targets[target]
    if not isinstance(info, dict) or info.get('enabled') is False:
        return {'ok': False, 'error': 'target_unavailable', 'state': 'rejected', 'changes_applied': False}

    def execute(command, timeout):
        return ssh_exec(info['host'], info.get('port', 22), info.get('username', 'admin'),
                        info.get('password', ''), command, timeout=timeout,
                        hostkey_sha256=info.get('hostkey_sha256'), key_path=info.get('key_path'))
    return exchange(args, info, execute)


def exchange(args, info, execute, worker_command=None):
    try:
        plan = prepare(args)  # Validate all steps locally before opening transport.
    except PlanError as exc:
        return {'ok': False, 'error': str(exc), 'state': 'rejected', 'changes_applied': False}
    host_key = str(info['host']) + ':' + str(info.get('port', 22))
    envelope = {'input': args, 'scope_id': hashlib.sha256(host_key.encode()).hexdigest()}
    allowed = {(index, phase): command for index, step in enumerate(plan['steps'])
               for phase, command in step.items()}
    results, failed, next_id, command_started = {}, False, 1, False
    command = worker_command or ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                                'vps', 'python3 -I /opt/harmonika-ai/config-transaction/worker.py']
    process = None
    deadline = time.monotonic() + 90
    received = b''
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        process.stdin.write(json.dumps(envelope).encode() + b'\n')
        process.stdin.flush()
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if not select.select([process.stdout], [], [], remaining)[0]:
                break
            chunk = process.stdout.read1(65536)
            if not chunk:
                break
            received += chunk
            # At most 64 bounded phase results; JSON escaping can expand Unicode.
            if len(received) > 2 * 1024 * 1024:
                raise ValueError('coordinator_response_limit')
            if b'\n' not in received:
                continue
            line, received = received.split(b'\n', 1)
            message = json.loads(line)
            if message.get('type') == 'result':
                result = message['result']
                if type(result) is not dict:
                    raise ValueError('invalid_result')
                if result.get('state') in ('preview', 'verified', 'applied_unverified', 'rolled_back'):
                    if result.get('plan_sha256') != plan['plan_sha256']:
                        raise ValueError('plan_changed')
                if result.get('state') in ('verified', 'applied_unverified'):
                    for item_index, step in enumerate(plan['steps']):
                        if not results.get((item_index, 'command'), {}).get('ok'):
                            raise ValueError('success_without_execution_evidence')
                        if 'verify' in step and not results.get((item_index, 'verify'), {}).get('ok'):
                            raise ValueError('success_without_verification')
                if result.get('state') == 'verified' and not all('verify' in s for s in plan['steps']):
                    raise ValueError('missing_verification_plan')
                if result.get('state') == 'rolled_back':
                    affected = [i for i in range(len(plan['steps'])) if (i, 'command') in results]
                    if not failed or not affected or any(
                            not results.get((i, 'rollback'), {}).get('ok') or
                            not results.get((i, 'rollback_verify'), {}).get('ok') for i in affected):
                        raise ValueError('rollback_without_evidence')
                return result
            index, phase = message.get('index'), message.get('phase')
            key = (index, phase)
            if (message.get('type') != 'execute' or message.get('id') != next_id
                    or type(index) is not int or key not in allowed or key in results
                    or message.get('command') != allowed[key] or plan['dry_run']
                    or type(message.get('timeout')) is not int or not 1 <= message['timeout'] <= 30):
                raise ValueError('unexpected_execution_request')
            if phase == 'command':
                if failed or any(not results.get((previous, 'command'), {}).get('ok') or
                                 ('verify' in plan['steps'][previous] and
                                  not results.get((previous, 'verify'), {}).get('ok'))
                                 for previous in range(index)):
                    raise ValueError('dependent_execution_rejected')
            elif phase == 'verify':
                if not results.get((index, 'command'), {}).get('ok') or failed:
                    raise ValueError('dependent_execution_rejected')
            elif phase == 'rollback':
                previous = results.get((index, 'command'), {})
                if not failed or previous.get('execution_state') != 'completed':
                    raise ValueError('unverified_compensation_rejected')
            elif phase == 'rollback_verify':
                if not results.get((index, 'rollback'), {}).get('ok'):
                    raise ValueError('dependent_execution_rejected')
            next_id += 1
            command_started = True
            result = execute(message['command'], message['timeout'])
            if type(result) is not dict:
                raise ValueError('invalid_executor_result')
            results[key] = result
            if not result.get('ok') or (phase in ('verify', 'rollback_verify') and (
                    result.get('output_truncated') or result.get('stderr_truncated'))):
                failed = True
            process.stdin.write(json.dumps({'id': message['id'], 'result': result}).encode() + b'\n')
            process.stdin.flush()
    except Exception:
        pass
    finally:
        if process:
            if process.stdin:
                process.stdin.close()  # EOF lets the private worker stop without retrying a command.
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()  # Only this adapter's own SSH client, never sshd/user terminals.
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            if process.stdout:
                process.stdout.close()
    return {'ok': False, 'error': 'config_coordinator_transport_failed',
            'state': 'needs_reconciliation' if command_started else 'not_started',
            'execution_state': 'unknown' if command_started else 'not_started',
            'safe_to_retry': False, 'changes_applied': command_started}

"""Bounded mechanics for matching a lost ACK to its exact reconciliation key."""
import re


def track(state, origin, method, route, status, fingerprint, dropped):
    incidents = state.setdefault('lost_ack_incidents', [])
    for incident in incidents:
        if incident['origin'] != origin or incident['reconciled']:
            continue
        if method == 'POST' and route == '/api/tickets':
            incident['submit_before_reconcile'] = True
        if method == 'GET' and route == '/api/requests/:key' and fingerprint == incident['request_key_hash']:
            incident['attempts'] += 1
            if status == 200:
                incident['reconciled'] = True
    if dropped:
        incidents.append({'origin': origin, 'request_key_hash': fingerprint, 'attempts': 0, 'reconciled': False, 'submit_before_reconcile': False})
        if len(incidents) > 32:
            state['incident_overflow'] = True
            state['lost_ack_incidents'] = incidents[-32:]


def is_reconciled(state):
    incidents = [x for x in state.get('lost_ack_incidents', []) if x.get('origin') == 'agent']
    return not state.get('incident_overflow') and bool(incidents) and all(
        isinstance(x.get('request_key_hash'), str)
        and re.fullmatch('[0-9a-f]{64}', x['request_key_hash']) is not None
        and x.get('reconciled') is True
        and not x.get('submit_before_reconcile')
        and 1 <= x.get('attempts', 0) <= 3
        for x in incidents
    )

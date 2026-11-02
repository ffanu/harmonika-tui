"""Harmonika trust & safety engine: defensive fraud, anti-bot and privacy audits.

Pure standard library. No network access, no source execution, no PII retention.
Every result is a rule-based candidate assessment over caller-supplied telemetry,
source text, cookie declarations and response headers. Nothing here proves that an
attack occurred, verifies a live CAPTCHA deployment, or certifies compliance.

Scope is strictly defensive and only for systems the operator owns: detection rules,
hardening recommendations, integration plans and privacy mitigations. The engine
intentionally contains no fingerprint spoofing, CAPTCHA solving, session rotation or
detection-evasion capability.
"""
import datetime
import hashlib
import json
import math
import re

__all__ = ['OPERATIONS', 'TrustError', 'call', 'capabilities']

ENGINE_NAME = 'harmonika-trust-shield'
ENGINE_VERSION = '1.0.0'
SCHEMA_VERSION = 1

OPERATIONS = frozenset((
    'trust.capabilities',
    'trust.fraud_risk',
    'trust.captcha_audit',
    'trust.privacy_audit',
    'trust.bot_defense_plan',
))

MAX_BYTES = 262144
MAX_DEPTH = 10
MAX_VISITS = 200000
MAX_EVENTS = 5000
MAX_FILES = 20
MAX_FILE_BYTES = 60000
MAX_FINDINGS = 200

SEVERITIES = ('critical', 'high', 'medium', 'low', 'info')
WEIGHTS = {'critical': 40, 'high': 25, 'medium': 12, 'low': 5, 'info': 0}

LIMITATIONS = (
    'Rule-based candidate assessment over supplied data; it is not proof of an attack.',
    'No live endpoint is contacted, no CAPTCHA is solved and no traffic is intercepted.',
    'Decisions are recommendations for a system the operator controls, not enforcement.',
    'Compliance certification, forensic attribution and legal conclusions are out of scope.',
    'This engine never produces fingerprint evasion, CAPTCHA solving or bypass tooling.',
)


class TrustError(ValueError):
    """Machine-coded error; the message never carries caller-supplied content."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise TrustError(code)


def _bounded(value):
    stack = [(value, 0)]
    visits = 0
    while stack:
        item, depth = stack.pop()
        visits += 1
        _need(depth <= MAX_DEPTH and visits <= MAX_VISITS, 'input_complexity_limit')
        kind = type(item)
        _need(kind in (dict, list, str, int, float, bool, type(None)), 'invalid_json')
        if kind is dict:
            _need(all(type(key) is str for key in item), 'invalid_json_key')
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif kind is list:
            stack.extend((child, depth + 1) for child in item)
        elif kind is str:
            _need(not any(0xD800 <= ord(char) <= 0xDFFF for char in item), 'invalid_unicode')
        elif kind is int:
            _need(item.bit_length() <= 128, 'invalid_integer')
        elif kind is float:
            _need(math.isfinite(item), 'invalid_number')
    try:
        raw = json.dumps(value, allow_nan=False, ensure_ascii=True).encode()
    except (TypeError, ValueError):
        raise TrustError('invalid_json') from None
    _need(len(raw) <= MAX_BYTES, 'input_byte_limit')


def _obj(value, allowed, required=()):
    _need(type(value) is dict, 'invalid_object')
    _need(set(value) <= set(allowed), 'invalid_fields')
    _need(set(required) <= set(value), 'missing_field')


def _text(value, maximum=1000):
    _need(type(value) is str and 1 <= len(value) <= maximum, 'invalid_text')
    _need(not any(ord(char) < 32 and char not in '\n\t\r' for char in value), 'invalid_text')
    return value


def _flag(value):
    _need(type(value) is bool, 'invalid_flag')
    return value


def _int(value, low=0, high=10 ** 9):
    _need(type(value) is int and low <= value <= high, 'invalid_integer')
    return value


def _strs(value, maximum=200):
    _need(type(value) is list and len(value) <= 300, 'invalid_list')
    return frozenset(_text(item, maximum) for item in value)


def _ref(kind, value):
    """Pseudonymous reference so raw identifiers never leave the engine."""
    raw = value if type(value) is str else str(value)
    return kind + ':ref_' + hashlib.sha256(raw.encode('utf-8', 'replace')).hexdigest()[:16]


def _digest(value):
    return hashlib.sha256(value.encode('utf-8', 'replace')).hexdigest()


def _parse_ts(value):
    _text(value, 64)
    candidate = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.datetime.fromisoformat(candidate)
    except ValueError:
        raise TrustError('invalid_timestamp') from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _finding(rule_id, severity, subject, observed, recommendation, evidence=None):
    _need(severity in SEVERITIES, 'invalid_severity')
    row = {
        'rule_id': rule_id,
        'severity': severity,
        'score': WEIGHTS[severity],
        'subject': subject,
        'observed': int(observed),
        'recommendation': recommendation,
        'verified': False,
    }
    if evidence:
        row['evidence_refs'] = sorted(set(evidence))[:20]
    return row


def _finalize(operation, findings, extra):
    deduped = {}
    for finding in findings:
        key = (finding['rule_id'], finding['subject'])
        current = deduped.get(key)
        if current is None or finding['observed'] >= current['observed']:
            deduped[key] = finding
    rows = sorted(deduped.values(), key=lambda r: (-r['score'], r['rule_id']))[:MAX_FINDINGS]
    scores = {}
    for row in rows:
        scores[row['subject']] = min(100, scores.get(row['subject'], 0) + row['score'])
    decisions = {'block': 0, 'challenge': 0, 'monitor': 0, 'allow': 0}
    subjects = []
    for subject, score in sorted(scores.items()):
        verdict = ('block' if score >= 70 else 'challenge' if score >= 40
                   else 'monitor' if score >= 20 else 'allow')
        decisions[verdict] += 1
        subjects.append({'subject': subject, 'risk_score': score,
                         'recommended_decision': verdict,
                         'rule_count': sum(1 for row in rows if row['subject'] == subject)})
    result = {
        'schema_version': SCHEMA_VERSION,
        'engine': ENGINE_NAME,
        'engine_version': ENGINE_VERSION,
        'operation': operation,
        'assessment_kind': 'rule_based_candidate',
        'attack_confirmed': False,
        'enforcement_performed': False,
        'telemetry_retained': False,
        'findings': rows,
        'subjects': subjects,
        'decisions': decisions,
        'limitations': list(LIMITATIONS),
    }
    result.update(extra)
    return result

# ------------------------------------------------------------------ fraud audit
EVENT_FIELDS = ('id', 'ts', 'kind', 'outcome', 'account', 'ip', 'asn', 'country', 'device_id',
                'session_id', 'ua', 'client_hints', 'email', 'amount_minor', 'currency',
                'card_bin', 'channel')
EVENT_KINDS = ('login', 'signup', 'password_reset', 'checkout', 'payment', 'api', 'other')
OUTCOMES = ('success', 'fail', 'not_found', 'challenge', 'error')
HINT_FIELDS = ('sec_ch_ua', 'sec_ch_ua_platform', 'sec_ch_ua_mobile', 'accept_language')
FRAUD_INPUT = ('events', 'window_minutes', 'thresholds', 'datacenter_asns', 'vpn_asns',
               'trusted_asns', 'known_devices', 'disposable_email_domains')
THRESHOLD_FIELDS = ('failed_logins_per_account', 'failed_logins_per_ip', 'account_breadth_per_ip',
                    'distinct_devices_per_account', 'distinct_cards_per_account',
                    'payment_failures_per_account', 'timing_jitter_ms', 'min_timing_events')
FRAUD_DEFAULTS = {
    'failed_logins_per_account': 5,
    'failed_logins_per_ip': 10,
    'account_breadth_per_ip': 5,
    'distinct_devices_per_account': 4,
    'distinct_cards_per_account': 3,
    'payment_failures_per_account': 3,
    'timing_jitter_ms': 40,
    'min_timing_events': 6,
}
FAIL_OUTCOMES = ('fail', 'not_found', 'error')
AUTH_KINDS = ('login', 'password_reset', 'signup')


def _events(raw):
    rows = []
    for item in _check_items(raw):
        _obj(item, EVENT_FIELDS, ('id', 'ts', 'kind', 'outcome'))
        row = {
            'id': _text(item['id'], 64),
            'ts': _parse_ts(item['ts']),
            'kind': _choice(item['kind'], EVENT_KINDS),
            'outcome': _choice(item['outcome'], OUTCOMES),
        }
        for field in ('account', 'ip', 'asn', 'country', 'device_id', 'session_id', 'email',
                      'card_bin', 'currency', 'channel'):
            if field in item:
                row[field] = _text(item[field], 120)
        if 'ua' in item:
            row['ua'] = _text(item['ua'], 400)
        if 'amount_minor' in item:
            row['amount_minor'] = _int(item['amount_minor'], 0, 10 ** 12)
        if 'client_hints' in item:
            _obj(item['client_hints'], HINT_FIELDS)
            hints = {}
            for key, value in item['client_hints'].items():
                hints[key] = _flag(value) if key in ('sec_ch_ua', 'sec_ch_ua_mobile') else _text(value, 120)
            row['client_hints'] = hints
        rows.append(row)
    return sorted(rows, key=lambda r: (r['ts'], r['id']))


def _choice(value, options):
    _need(type(value) is str and value in options, 'invalid_enum')
    return value


def _check_items(raw):
    _need(type(raw) is list, 'invalid_events')
    _need(len(raw) <= MAX_EVENTS, 'too_many_events')
    return raw


def _platform_from_ua(ua):
    for marker, name in (('Windows NT', 'windows'), ('Android', 'android'), ('iPhone', 'ios'),
                         ('iPad', 'ios'), ('CrOS', 'chromeos'), ('Macintosh', 'macos'),
                         ('Linux', 'linux')):
        if marker in ua:
            return name
    return None


def _by(rows, field):
    groups = {}
    for row in rows:
        if field in row:
            groups.setdefault(row[field], []).append(row)
    return groups


def _fraud_risk(data):
    _obj(data, FRAUD_INPUT)
    rows = _events(data.get('events', []))
    _need(rows, 'no_events')
    thresholds = dict(FRAUD_DEFAULTS)
    if 'thresholds' in data:
        _obj(data['thresholds'], THRESHOLD_FIELDS)
        for key, value in data['thresholds'].items():
            thresholds[key] = _int(value, 1, 100000)
    window = _int(data.get('window_minutes', 15), 1, 1440)
    datacenter = _strs(data.get('datacenter_asns', []), 40)
    vpn = _strs(data.get('vpn_asns', []), 40)
    trusted = _strs(data.get('trusted_asns', []), 40)
    disposable = frozenset(item.lower() for item in _strs(data.get('disposable_email_domains', []), 120))
    known_devices = {}
    raw_known = data.get('known_devices', {})
    _need(type(raw_known) is dict, 'invalid_known_devices')
    for account, devices in raw_known.items():
        known_devices[_text(account, 120)] = _strs(devices, 120)

    anchor = rows[-1]['ts']
    horizon = anchor - datetime.timedelta(minutes=window)
    recent = [row for row in rows if row['ts'] >= horizon]
    findings = []
    per_account = _by(recent, 'account')
    per_ip = _by(recent, 'ip')
    per_session = _by(rows, 'session_id')

    for account, group in per_account.items():
        subject = _ref('account', account)
        failures = [r for r in group if r['kind'] in AUTH_KINDS and r['outcome'] in FAIL_OUTCOMES]
        if len(failures) >= thresholds['failed_logins_per_account']:
            findings.append(_finding(
                'FRAUD.LOGIN.VELOCITY', 'high', subject, len(failures),
                'Pasang exponential backoff, verifikasi tambahan (OTP/email) dan blokir sementara setelah batas kegagalan.',
                [r['id'] for r in failures]))
        devices = {r['device_id'] for r in group if 'device_id' in r}
        if len(devices) >= thresholds['distinct_devices_per_account']:
            findings.append(_finding(
                'FRAUD.DEVICE.FANOUT', 'medium', subject, len(devices),
                'Batasi jumlah perangkat aktif per akun dan wajibkan verifikasi perangkat baru.'))
        allowed = known_devices.get(account)
        if allowed is not None and devices - allowed:
            findings.append(_finding(
                'FRAUD.DEVICE.BINDING_DRIFT', 'medium', subject, len(devices - allowed),
                'Wajibkan step-up verification untuk perangkat tidak terdaftar sebelum memberi akses penuh.'))
        cards = {r['card_bin'] for r in group if 'card_bin' in r}
        payment_failures = [r for r in group if r['kind'] in ('payment', 'checkout')
                            and r['outcome'] in FAIL_OUTCOMES]
        if len(cards) >= thresholds['distinct_cards_per_account']:
            findings.append(_finding(
                'FRAUD.PAYMENT.CARD_TESTING', 'critical', subject, len(cards),
                'Tolak rangkaian uji kartu: aktifkan 3DS/SCA, blokir BIN yang berulang gagal, kirim ke review manual.',
                [r['id'] for r in payment_failures]))
        elif len(payment_failures) >= thresholds['payment_failures_per_account']:
            findings.append(_finding(
                'FRAUD.PAYMENT.FAILURE_STREAK', 'high', subject, len(payment_failures),
                'Batasi percobaan pembayaran per akun dan minta kanal pembayaran alternatif.',
                [r['id'] for r in payment_failures]))
        amounts = sorted(r['amount_minor'] for r in group if 'amount_minor' in r)
        if len(amounts) >= 5:
            median = amounts[len(amounts) // 2]
            if median > 0 and amounts[-1] >= median * 5:
                findings.append(_finding(
                    'FRAUD.PAYMENT.AMOUNT_ANOMALY', 'medium', subject, amounts[-1],
                    'Kirim transaksi bernilai jauh di atas pola akun ke review manual sebelum diproses.'))
        countries = [(r['ts'], r['country']) for r in group if 'country' in r]
        for previous, current in zip(countries, countries[1:]):
            if previous[1] != current[1] and (current[0] - previous[0]).total_seconds() <= 3600:
                findings.append(_finding(
                    'FRAUD.GEO.IMPOSSIBLE_TRAVEL', 'high', subject, 1,
                    'Minta verifikasi ulang saat lokasi berubah cepat; kunci sesi lama dan catat perangkat baru.'))
                break

    for ip, group in per_ip.items():
        subject = _ref('ip', ip)
        accounts = {r['account'] for r in group if 'account' in r}
        failures = [r for r in group if r['outcome'] in FAIL_OUTCOMES]
        if len(accounts) >= thresholds['account_breadth_per_ip'] and len(failures) >= len(accounts):
            findings.append(_finding(
                'FRAUD.CREDENTIAL_STUFFING', 'critical', subject, len(accounts),
                'Blokir IP sementara di edge, paksa reset kredensial akun terdampak, pantau ASN sumbernya.',
                [r['id'] for r in failures]))
        unknown = {r['account'] for r in group
                   if r['outcome'] in ('fail', 'not_found') and 'account' in r}
        if len(unknown) >= thresholds['account_breadth_per_ip']:
            findings.append(_finding(
                'FRAUD.ACCOUNT.ENUMERATION', 'medium', subject, len(unknown),
                'Kembalikan respons seragam untuk akun tidak dikenal dan tambahkan rate limit per IP.'))
        if len(failures) >= thresholds['failed_logins_per_ip']:
            findings.append(_finding(
                'FRAUD.LOGIN.VELOCITY', 'high', subject, len(failures),
                'Terapkan rate limit per IP dan tantangan adaptif saat ambang kegagalan tercapai.',
                [r['id'] for r in failures]))
        if len(group) >= 8 and len(failures) / len(group) >= 0.8:
            findings.append(_finding(
                'FRAUD.PASSWORD.SPRAY', 'high', subject, len(failures),
                'Deteksi password spraying: batasi percobaan lintas akun per IP/ASN dan notifikasi pemilik akun.'))
        asns = {r.get('asn') for r in group}
        if asns & vpn:
            findings.append(_finding(
                'FRAUD.NETWORK.VPN_PROXY', 'medium', subject, len(asns & vpn),
                'Minta verifikasi tambahan untuk trafik dari VPN/proxy publik sebelum transaksi bernilai.'))
        elif (asns & datacenter) - trusted:
            findings.append(_finding(
                'FRAUD.NETWORK.DATACENTER', 'low', subject, len((asns & datacenter) - trusted),
                'Turunkan kepercayaan sinyal dari ASN datacenter; tambahkan tantangan adaptif pada endpoint sensitif.'))

    for row in recent:
        email = row.get('email')
        if email and '@' in email:
            if email.rsplit('@', 1)[1].lower() in disposable:
                findings.append(_finding(
                    'FRAUD.EMAIL.DISPOSABLE', 'low', _ref('account', row.get('account', email)), 1,
                    'Wajibkan verifikasi email sebelum aktivasi fitur bernilai dan catat domain sekali pakai.'))
        hints = row.get('client_hints') or {}
        ua = row.get('ua', '')
        chromium = 'Chrome/' in ua or 'Chromium' in ua or 'CriOS' in ua
        if chromium and not hints.get('sec_ch_ua'):
            findings.append(_finding(
                'FRAUD.CLIENT_HINTS.MISSING', 'medium', 'telemetry:client_hints', 1,
                'Catat header Sec-CH-UA di server; ketiadaannya pada klaim Chromium adalah sinyal ketidakcocokan klien.',
                [row['id']]))
        claimed = _platform_from_ua(ua)
        hinted = hints.get('sec_ch_ua_platform')
        if chromium and claimed and hinted and claimed not in hinted.lower():
            findings.append(_finding(
                'FRAUD.CLIENT_HINTS.MISMATCH', 'high', 'telemetry:client_hints', 1,
                'User-Agent dan Sec-CH-UA-Platform tidak konsisten; perlakukan sebagai klien termanipulasi.',
                [row['id']]))
        if ua and not hints.get('accept_language') and row['kind'] in AUTH_KINDS:
            findings.append(_finding(
                'FRAUD.HEADER.ACCEPT_LANGUAGE_MISSING', 'low', 'telemetry:headers', 1,
                'Catat Accept-Language pada endpoint autentikasi; ketiadaannya menaikkan risiko klien otomatis.',
                [row['id']]))

    for session, group in per_session.items():
        subject = _ref('session', session)
        ips = {r['ip'] for r in group if 'ip' in r}
        devices = {r['device_id'] for r in group if 'device_id' in r}
        if len(ips) > 1 or len(devices) > 1:
            findings.append(_finding(
                'FRAUD.SESSION.SHARING', 'high', subject, max(len(ips), len(devices)),
                'Ikat sesi ke perangkat/IP awal, rotasi token saat perubahan, dan paksa autentikasi ulang.',
                [r['id'] for r in group[:20]]))
        if len(group) >= thresholds['min_timing_events']:
            deltas = [(later['ts'] - earlier['ts']).total_seconds() * 1000
                      for earlier, later in zip(group, group[1:])]
            mean = sum(deltas) / len(deltas)
            spread = math.sqrt(sum((value - mean) ** 2 for value in deltas) / len(deltas))
            if mean <= 3000 and spread <= thresholds['timing_jitter_ms']:
                findings.append(_finding(
                    'FRAUD.AUTOMATION.TIMING', 'high', subject, len(group),
                    'Pola interaksi terlalu seragam; tambahkan tantangan adaptif dan pemeriksaan perilaku klien.',
                    [r['id'] for r in group[:20]]))

    return _finalize('trust.fraud_risk', findings, {
        'events_analyzed': len(rows),
        'events_in_window': len(recent),
        'window_minutes': window,
        'thresholds': thresholds,
        'identifier_handling': 'pseudonymous_refs_only',
    })

# ---------------------------------------------------------------- captcha audit
CAPTCHA_INPUT = ('provider', 'client', 'server', 'rate_limit', 'other')
CAPTCHA_PROVIDERS = ('turnstile', 'hcaptcha', 'recaptcha_v2', 'recaptcha_v3', 'custom', 'none')
CLIENT_FIELDS = ('site_key_present', 'widget_on_auth', 'callback_on_success', 'hidden_field_submitted')
SERVER_FIELDS = ('siteverify_called', 'secret_from_env', 'token_single_use', 'expiry_seconds',
                 'remoteip_checked', 'hostname_checked', 'action_checked', 'score_threshold')
RATE_FIELDS = ('per_ip_per_minute', 'per_account_per_hour')
OTHER_FIELDS = ('honeypot', 'csrf_token', 'content_security_policy', 'logs_bot_signals')
PROVIDER_DOCS = {
    'turnstile': 'https://developers.cloudflare.com/turnstile/get-started/server-side-validation/',
    'hcaptcha': 'https://docs.hcaptcha.com/#server',
    'recaptcha_v2': 'https://developers.google.com/recaptcha/docs/verify',
    'recaptcha_v3': 'https://developers.google.com/recaptcha/docs/v3',
    'custom': 'internal-specification',
    'none': 'no-provider-configured',
}


def _captcha_audit(data):
    _obj(data, CAPTCHA_INPUT, ('provider',))
    provider = _choice(data['provider'], CAPTCHA_PROVIDERS)
    client = data.get('client', {})
    server = data.get('server', {})
    limits = data.get('rate_limit', {})
    other = data.get('other', {})
    for block, allowed in ((client, CLIENT_FIELDS), (server, SERVER_FIELDS),
                           (limits, RATE_FIELDS), (other, OTHER_FIELDS)):
        if block:
            _obj(block, allowed)
    findings = []
    subject = 'captcha:' + provider

    if provider == 'none':
        findings.append(_finding(
            'CAPTCHA.DEPLOYMENT.MISSING', 'high', subject, 1,
            'Pasang CAPTCHA/Turnstile pada form autentikasi, pendaftaran, dan reset password, lalu verifikasi token di server.'))
    if provider != 'none' and not client.get('site_key_present', False):
        findings.append(_finding(
            'CAPTCHA.CLIENT.WIDGET_MISSING', 'high', subject, 1,
            'Muat widget CAPTCHA di halaman autentikasi dan kirim token hasilnya bersama form.'))
    if provider != 'none' and not client.get('widget_on_auth', False):
        findings.append(_finding(
            'CAPTCHA.CLIENT.NOT_ON_AUTH', 'medium', subject, 1,
            'Tempatkan widget pada semua alur sensitif: login, daftar, reset password, dan checkout.'))
    if provider != 'none' and not server.get('siteverify_called', False):
        findings.append(_finding(
            'CAPTCHA.SERVER.VERIFICATION_MISSING', 'critical', subject, 1,
            'Wajib panggil endpoint siteverify dari server dengan secret key; token yang hanya dicek di klien dapat dipalsukan.'))
    if not server.get('secret_from_env', False):
        findings.append(_finding(
            'CAPTCHA.SECRET.STORAGE', 'critical', subject, 1,
            'Simpan secret CAPTCHA di secret manager/environment, jangan di kode sumber atau bundle frontend.'))
    if not server.get('token_single_use', False):
        findings.append(_finding(
            'CAPTCHA.TOKEN.NO_SINGLE_USE', 'high', subject, 1,
            'Tolak token yang sudah diverifikasi: catat hash token selama masa berlaku dan tolak pemakaian ulang.'))
    expiry = server.get('expiry_seconds', 0)
    if type(expiry) is not int or expiry <= 0:
        findings.append(_finding(
            'CAPTCHA.TOKEN.EXPIRY_UNDEFINED', 'high', subject, 1,
            'Terapkan batas umur token (umumnya <= 300 detik) dan tolak token kedaluwarsa.'))
    elif expiry > 600:
        findings.append(_finding(
            'CAPTCHA.TOKEN.EXPIRY_LONG', 'medium', subject, expiry,
            'Perpendek masa berlaku token; token berumur panjang mudah dipakai ulang oleh penyerang.'))
    if server.get('siteverify_called') and not server.get('hostname_check', True):
        findings.append(_finding(
            'CAPTCHA.SERVER.HOSTNAME_UNVERIFIED', 'medium', subject, 1,
            'Validasi nilai hostname pada respons verifikasi agar token dari domain lain tidak diterima.'))
    if provider == 'recaptcha_v3':
        if not server.get('action_checked', False):
            findings.append(_finding(
                'CAPTCHA.SERVER.ACTION_UNCHECKED', 'high', subject, 1,
                'Cocokkan nilai action pada respons verifikasi dengan aksi yang diharapkan.'))
        score = server.get('score_threshold')
        if type(score) not in (int, float) or not 0 <= float(score) <= 1:
            findings.append(_finding(
                'CAPTCHA.SERVER.SCORE_UNDEFINED', 'high', subject, 1,
                'Tetapkan ambang skor (mis. 0.5) dan perlakukan skor rendah sebagai tantangan tambahan.'))
        elif float(score) < 0.5:
            findings.append(_finding(
                'CAPTCHA.SERVER.SCORE_LOW', 'high', subject, 1,
                'Naikkan ambang skor ke minimal 0.5 untuk alur autentikasi dan pembayaran.'))
    if not server.get('remoteip_checked', False):
        findings.append(_finding(
            'CAPTCHA.SERVER.REMOTEIP_OMITTED', 'low', subject, 1,
            'Kirim remoteip saat verifikasi untuk meningkatkan akurasi penilaian provider.'))
    per_ip = limits.get('per_ip_per_minute', 0)
    if type(per_ip) is not int or per_ip < 5:
        findings.append(_finding(
            'CAPTCHA.RATE_LIMIT.LOW', 'high', subject, per_ip if type(per_ip) is int else 0,
            'Terapkan rate limit per IP (minimal 5-10 permintaan/menit pada endpoint autentikasi) di edge dan aplikasi.'))
    per_account = limits.get('per_account_per_hour', 0)
    if type(per_account) is not int or per_account < 10:
        findings.append(_finding(
            'CAPTCHA.RATE_LIMIT.ACCOUNT_UNBOUNDED', 'medium', subject,
            per_account if type(per_account) is int else 0,
            'Batasi percobaan per akun (mis. 10/jam) dan kunci sementara setelah ambang terlampaui.'))
    if not other.get('honeypot', False):
        findings.append(_finding(
            'CAPTCHA.FIELD.HONEYPOT_MISSING', 'low', subject, 1,
            'Tambahkan field honeypot tersembunyi; bot umumnya mengisinya sehingga dapat ditolak murah.'))
    if not other.get('csrf_token', False):
        findings.append(_finding(
            'CAPTCHA.FIELD.CSRF_MISSING', 'high', subject, 1,
            'Pasang token CSRF pada form autentikasi: CAPTCHA tidak menggantikan proteksi CSRF.'))
    if not other.get('content_security_policy', False):
        findings.append(_finding(
            'CAPTCHA.HEADER.CSP_MISSING', 'medium', subject, 1,
            'Terapkan CSP yang mengizinkan hanya domain provider CAPTCHA dan script milik sendiri.'))
    if not other.get('logs_bot_signals', False):
        findings.append(_finding(
            'CAPTCHA.OBSERVABILITY.SIGNALS_UNLOGGED', 'medium', subject, 1,
            'Catat sinyal bot (skor, action, hasil verifikasi, ASN) tanpa menyimpan PII berlebih untuk tuning aturan.'))

    checklist = [
        'Widget dimuat hanya pada alur sensitif dan token dikirim bersama form.',
        'Server memanggil siteverify dengan secret dari secret manager, bukan dari kode/bundle.',
        'Token diverifikasi sekali, dengan batas umur, hostname, dan action yang diharapkan.',
        'Rate limit per IP dan per akun berjalan sebelum pekerjaan mahal dilakukan.',
        'Kegagalan CAPTCHA dicatat sebagai metrik dan tidak membocorkan detail ke pengguna.',
    ]
    return _finalize('trust.captcha_audit', findings, {
        'provider': provider,
        'provider_reference': PROVIDER_DOCS[provider],
        'live_verification_performed': False,
        'recommended_flow': [
            'Render widget dengan site key publik.',
            'Terima token pada endpoint server melalui POST body.',
            'Verifikasi ke endpoint penyedia dengan secret + remoteip.',
            'Tolak token invalid/expired/reused, lalu lanjutkan otorisasi aplikasi.',
        ],
        'checklist': checklist,
    })

# ---------------------------------------------------------------- privacy audit
PRIVACY_INPUT = ('files', 'cookies', 'headers', 'note')
COOKIE_FIELDS = ('name', 'secure', 'httponly', 'samesite', 'max_age_days')
SAMESITE_VALUES = ('lax', 'strict', 'none', 'unset')
HEADER_KEYS = ('content-security-policy', 'strict-transport-security', 'referrer-policy',
               'permissions-policy', 'cross-origin-opener-policy', 'cross-origin-embedder-policy',
               'x-content-type-options', 'cache-control')
SIGNAL_CATEGORIES = ('fingerprint_surface', 'cookie_hardening', 'header_hardening', 'storage', 'dependency')
SOURCE_RULES = (
    ('PRIVACY.NAVIGATOR.SURFACE', re.compile(r'navigator\s*\.\s*(userAgentData|userAgent|platform|hardwareConcurrency|deviceMemory|plugins|languages|mimeTypes)'),
     'low', 'Setiap pembacaan properti navigator ini menambah entropi; minta hanya yang benar-benar dipakai fitur.',
     'Batasi pembacaan ke satu properti yang diperlukan; jangan menggabungkan banyak sinyal untuk identifikasi.'),
    ('PRIVACY.CANVAS.SIGNAL', re.compile(r'\b(?:getImageData|toDataURL|toBlob|measureText)\s*\('),
     'medium', 'Operasi pembacaan canvas dipakai sebagai sinyal sidik jari lintas situs.',
     'Gunakan canvas hanya untuk rendering yang terlihat pengguna; jangan menurunkan hash dari piksel.'),
    ('PRIVACY.WEBGL.RENDERER', re.compile(r'UNMASKED_(?:RENDERER|VENDOR)_WEBGL'),
     'high', 'Query UNMASKED_RENDERER/VENDOR WEBGL mengekspos model GPU perangkat.',
     'Hapus query unmasked renderer; cukup pakai nama extension bila fitur grafis membutuhkannya.'),
    ('PRIVACY.AUDIO.CONTEXT', re.compile(r'\b(?:OfflineAudioContext|AudioContext|DynamicsCompressorNode)\b'),
     'medium', 'Audio fingerprint memakai karakteristik pemrosesan audio perangkat.',
     'Jika tidak ada fitur audio, jangan inisialisasi AudioContext; bila ada, jalankan atas aksi pengguna saja.'),
    ('PRIVACY.FONT.PROBING', re.compile(r'document\s*\.\s*fonts|getComputedStyle\([^)]*\)\s*\.\s*fontFamily|FontFaceObserver'),
     'low', 'Enumerasi font dapat menjadi sinyal perangkat yang stabil.',
     'Gunakan font stack tetap; hindari mengukur banyak font untuk menyimpulkan daftar font pengguna.'),
    ('PRIVACY.RTCP.LOCAL_ADDRESS', re.compile(r'\bRTCPeerConnection\b'),
     'high', 'STUN/ICE dapat membocorkan alamat IP lokal bila kebijakan host tidak dikunci.',
     'Setel iceTransportPolicy ke relay dan batasi ICE server pada layanan panggilan yang disetujui.'),
    ('PRIVACY.SCREEN.ENTROPY', re.compile(r'screen\s*\.\s*(colorDepth|pixelDepth|availWidth|availHeight|width|height)'),
     'low', 'Resolusi dan kedalaman warna dipakai sebagai bagian sidik jari.',
     'Gunakan nilai ini hanya untuk penyesuaian tata letak, bukan untuk membuat identitas perangkat.'),
    ('PRIVACY.TIMEZONE.PROBE', re.compile(r'Intl\s*\.\s*DateTimeFormat\s*\(\s*\)\s*\.\s*resolvedOptions'),
     'low', 'Zona waktu dapat dipakai menyimpulkan lokasi pengguna.',
     'Gunakan zona waktu hanya untuk format tanggal yang tampil, dan dokumentasikan penggunaannya.'),
    ('PRIVACY.STORAGE.TOKEN', re.compile(r'localStorage\s*\.\s*setItem\s*\(\s*[\'"][^\'"]*(?:token|jwt|auth|session)', re.I),
     'high', 'Token di localStorage dapat dibaca script mana pun di origin yang sama.',
     'Simpan sesi pada cookie HttpOnly+Secure+SameSite, bukan localStorage.'),
    ('PRIVACY.THIRDPARTY.SCRIPT', re.compile(r'<script[^>]+src=[\'"](?:https?:)?//', re.I),
     'medium', 'Script pihak ketiga memperluas permukaan data yang dapat dikumpulkan.',
     'Batasi domain script dengan CSP, muat setelah interaksi, dan tinjau data yang dikirim ke pihak ketiga.'),
    ('PRIVACY.DATA.PII_IN_URL', re.compile(r'[?&](?:email|phone|msisdn|otp|token|api_?key)=', re.I),
     'high', 'Data pribadi di URL tercatat pada log server, proxy, dan riwayat browser.',
     'Pindahkan data pribadi ke body POST; jangan pernah menaruhnya pada query string.'),
)


def _privacy_audit(data):
    _obj(data, PRIVACY_INPUT)
    files = data.get('files', [])
    _need(type(files) is list and len(files) <= MAX_FILES, 'invalid_files')
    findings = []
    scanned = 0
    for item in files:
        _obj(item, ('path', 'content'), ('path', 'content'))
        path = _text(item['path'], 240)
        content = _text(item['content'], MAX_FILE_BYTES)
        scanned += 1
        subject = 'file:ref_' + _digest(path)[:12]
        for rule_id, pattern, severity, description, mitigation in SOURCE_RULES:
            hits = pattern.findall(content)
            if hits:
                findings.append(_finding(rule_id, severity, subject, len(hits), mitigation))

    cookies = data.get('cookies', [])
    _need(type(cookies) is list and len(cookies) <= 200, 'invalid_cookies')
    for cookie in cookies:
        _obj(cookie, COOKIE_FIELDS, ('name',))
        name = _text(cookie['name'], 120)
        subject = 'cookie:ref_' + _digest(name)[:12]
        samesite = (cookie.get('samesite') or 'unset').lower()
        _need(samesite in SAMESITE_VALUES, 'invalid_samesite')
        secure = bool(cookie.get('secure', False))
        httponly = bool(cookie.get('httponly', False))
        if not secure:
            findings.append(_finding(
                'PRIVACY.COOKIE.MISSING_SECURE', 'high', subject, 1,
                'Tambahkan atribut Secure agar cookie tidak terkirim lewat koneksi non-TLS.'))
        if not httponly:
            findings.append(_finding(
                'PRIVACY.COOKIE.MISSING_HTTPONLY', 'high', subject, 1,
                'Tambahkan HttpOnly pada cookie sesi agar tidak dapat dibaca JavaScript.'))
        if samesite == 'none' and not secure:
            findings.append(_finding(
                'PRIVACY.COOKIE.SAMESITE_NONE_INSECURE', 'critical', subject, 1,
                'SameSite=None wajib disertai Secure; tanpa itu cookie lintas situs terekspos.'))
        if samesite == 'unset':
            findings.append(_finding(
                'PRIVACY.COOKIE.SAMESITE_UNSET', 'medium', subject, 1,
                'Tetapkan SameSite=Lax (atau Strict untuk cookie sensitif) secara eksplisit.'))
        days = cookie.get('max_age_days', 0)
        if type(days) is int and days > 180:
            findings.append(_finding(
                'PRIVACY.COOKIE.LONG_LIFETIME', 'medium', subject, days,
                'Perpendek masa hidup cookie sesi dan rotasi token saat login ulang.'))
        if not name.startswith(('__Host-', '__Secure-')):
            findings.append(_finding(
                'PRIVACY.COOKIE.PREFIX_ABSENT', 'low', subject, 1,
                'Gunakan prefix __Host-/__Secure- agar browser menolak cookie yang disisipkan domain lain.'))

    headers = data.get('headers', {})
    if headers:
        _obj(headers, HEADER_KEYS)
        lowered = {k.lower(): str(v) for k, v in headers.items()}
        csp = lowered.get('content-security-policy', '')
        if not csp:
            findings.append(_finding(
                'SECURITY.HEADER.CSP_MISSING', 'high', 'headers:policy', 1,
                'Tambahkan Content-Security-Policy yang membatasi script, frame, dan koneksi ke origin tepercaya.'))
        else:
            if 'unsafe-inline' in csp and "'nonce-" not in csp:
                findings.append(_finding(
                    'SECURITY.CSP.UNSAFE_INLINE', 'high', 'telemetry:headers', 1,
                    'Ganti unsafe-inline dengan nonce/hash agar injeksi script tidak lolos.'))
            if 'unsafe-eval' in csp:
                findings.append(_finding(
                    'SECURITY.CSP.UNSAFE_EVAL', 'medium', 'telemetry:headers', 1,
                    'Hindari unsafe-eval; refaktor kode yang memerlukan eval dinamis.'))
        if 'strict-transport-security' not in lowered:
            findings.append(_finding(
                'SECURITY.HEADER.HSTS_MISSING', 'high', 'telemetry:headers', 1,
                'Aktifkan HSTS minimal max-age=15552000; includeSubDomains setelah seluruh subdomain siap TLS.'))
        if 'referrer-policy' not in lowered:
            findings.append(_finding(
                'SECURITY.HEADER.REFERRER_POLICY_MISSING', 'low', 'telemetry:headers', 1,
                'Setel Referrer-Policy strict-origin-when-cross-origin agar URL internal tidak bocor.'))
        if 'permissions-policy' not in lowered:
            findings.append(_finding(
                'SECURITY.HEADER.PERMISSIONS_POLICY_MISSING', 'medium', 'telemetry:headers', 1,
                'Matikan fitur browser yang tidak dipakai (camera, microphone, geolocation) lewat Permissions-Policy.'))
        if 'cross-origin-opener-policy' not in lowered:
            findings.append(_finding(
                'SECURITY.HEADER.COOP_MISSING', 'low', 'telemetry:headers', 1,
                'Tambahkan Cross-Origin-Opener-Policy: same-origin untuk mengisolasi jendela lintas origin.'))
        if lowered.get('x-content-type-options', '').lower() != 'nosniff':
            findings.append(_finding(
                'SECURITY.HEADER.NOSNIFF_MISSING', 'low', 'telemetry:headers', 1,
                'Kirim X-Content-Type-Options: nosniff untuk mencegah sniffing tipe konten.'))
        cache = lowered.get('cache-control', '')
        if 'no-store' not in cache.lower():
            findings.append(_finding(
                'SECURITY.HEADER.CACHE_CONTROL_WEAK', 'medium', 'telemetry:headers', 1,
                'Gunakan Cache-Control: no-store pada respons terautentikasi agar tidak tersimpan di cache bersama.'))

    categories = {}
    for row in findings:
        categories[row['rule_id'].split('.')[0]] = categories.get(row['rule_id'].split('.')[0], 0) + 1
    return _finalize('trust.privacy_audit', findings, {
        'files_scanned': scanned,
        'cookies_reviewed': len(cookies),
        'headers_reviewed': len(headers),
        'categories': categories,
        'data_minimization_rules': [
            'Kumpulkan hanya sinyal yang dibutuhkan fitur yang terlihat pengguna.',
            'Jangan menggabungkan banyak sinyal perangkat menjadi identifier persisten.',
            'Simpan hasil analisis agregat, bukan data mentah per pengguna.',
            'Sediakan jalur opt-out analitik dan dokumentasikan retensinya.',
        ],
        'requests_modified': False,
    })


# ------------------------------------------------------------ anti-abuse plan
PLAN_INPUT = ('surface', 'stack', 'exposure', 'constraints')
PLAN_SURFACES = ('auth', 'checkout', 'public_api', 'signup', 'mixed')
PLAN_SURFACES_DEFAULT = ['login', 'register', 'reset-password']
PLAN_STAGES = (
    ('edge', [
        'Rate limit per IP dan per ASN pada endpoint autentikasi dan pembayaran.',
        'Tantangan adaptif (Turnstile/hCaptcha) yang aktif hanya setelah ambang risiko terlampaui.',
        'Blokir ASN datacenter/VPN pada endpoint sensitif setelah data menunjukkan penyalahgunaan.',
    ]),
    ('identity', [
        'Autentikasi dua faktor untuk akun berisiko tinggi dan operasi perubahan kredensial.',
        'Deteksi kredensial bocor saat login (cek hash terhadap daftar bocor milik Anda sendiri).',
        'Batasi percobaan per akun, aktifkan lockout progresif dan notifikasi login baru.',
    ]),
    ('session', [
        'Cookie sesi HttpOnly + Secure + SameSite, dengan rotasi token setelah login.',
        'Ikuti perangkat tepercaya dan wajibkan verifikasi ulang saat perangkat/IP berubah.',
        'Audit sesi aktif dan cabut sesi pada perubahan kredensial.',
    ]),
    ('payments', [
        '3DS/SCA wajib, velocity check per akun dan per instrumen pembayaran.',
        'Review manual otomatis untuk nominal jauh di atas pola akun.',
        'Simpan hanya hash instrumen pembayaran, bukan nomor kartu.',
    ]),
    ('observability', [
        'Metrik: rasio login gagal, distribusi skor CAPTCHA, akun per IP, dan tingkat blokir.',
        'Log sinyal bot tanpa PII berlebih, dengan retensi terbatas dan akses terbatas.',
        'Dashboard alert untuk lonjakan credential stuffing dan card testing.',
    ]),
    ('incident', [
        'Runbook: deteksi, pembatasan sementara, notifikasi pemilik akun, dan pemulihan.',
        'Prosedur reset kredensial massal yang aman bila stuffing terdeteksi.',
        'Post-mortem dengan metrik sebelum/sesudah kontrol baru.',
    ]),
)
EXCLUDED_SHORTCUTS = (
    'Tidak ada pembuatan sidik jari palsu atau spoofing perangkat.',
    'Tidak ada penyelesaian/penerobosan CAPTCHA atau token challenge.',
    'Tidak ada rotasi IP/proxy untuk menyamarkan asal trafik.',
    'Tidak ada teknik menghindari radar fraud detection pihak lain.',
    'Tidak ada scraping atau otomasi yang melanggar ketentuan layanan pihak ketiga.',
)


def _bot_defense_plan(data):
    _obj(data, PLAN_INPUT, ('surface',))
    surface = _choice(data['surface'], PLAN_SURFACES)
    stack = _text(data.get('stack', 'generic'), 80)
    exposure = data.get('exposure', {})
    _obj(exposure, ('login_per_minute', 'has_payment', 'public_signup', 'mobile_clients'))
    stages = []
    for name, controls in PLAN_STAGES:
        stages.append({'stage': name, 'controls': list(controls)})
    return {
        'schema_version': SCHEMA_VERSION,
        'engine': ENGINE_NAME,
        'engine_version': ENGINE_VERSION,
        'operation': 'trust.bot_defense_plan',
        'surface': surface,
        'stack': stack,
        'protected_flows': PLAN_SURFACES_DEFAULT,
        'stages': stages,
        'kpis': [
            'Rasio login gagal per akun dan per IP (harian).',
            'Persentase permintaan autentikasi yang ditantang vs diblokir.',
            'Waktu deteksi sampai pembatasan (menit).',
            'Tingkat keberhasilan recovery akun terdampak.',
        ],
        'excluded_shortcuts': list(EXCLUDED_SHORTCUTS),
        'implementation_performed': False,
        'limitations': list(LIMITATIONS),
    }

# ---------------------------------------------------------------------- registry
SCHEMAS = {
    'trust.capabilities': {'type': 'object', 'properties': {}, 'required': [],
                           'additionalProperties': False},
    'trust.fraud_risk': {
        'type': 'object',
        'properties': {
            'events': {'type': 'array', 'maxItems': MAX_EVENTS, 'items': {
                'type': 'object',
                'properties': {name: {'type': 'string', 'maxLength': 400} for name in
                               ('id', 'ts', 'kind', 'outcome', 'account', 'ip', 'asn', 'country',
                                'device_id', 'session_id', 'ua', 'email', 'card_bin',
                                'currency', 'channel')},
                'required': ['id', 'ts', 'kind', 'outcome'],
                'additionalProperties': False}},
            'window_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 1440},
            'thresholds': {'type': 'object', 'additionalProperties': {'type': 'integer'}},
            'datacenter_asns': {'type': 'array', 'items': {'type': 'string'}},
            'vpn_asns': {'type': 'array', 'items': {'type': 'string'}},
            'trusted_asns': {'type': 'array', 'items': {'type': 'string'}},
            'disposable_email_domains': {'type': 'array', 'items': {'type': 'string'}},
            'known_devices': {'type': 'object', 'additionalProperties': {
                'type': 'array', 'items': {'type': 'string'}}},
        },
        'required': ['events'],
        'additionalProperties': False,
    },
    'trust.captcha_audit': {
        'type': 'object',
        'properties': {
            'provider': {'type': 'string', 'enum': list(CAPTCHA_PROVIDERS)},
            'client': {'type': 'object'},
            'server': {'type': 'object'},
            'rate_limit': {'type': 'object'},
            'other': {'type': 'object'},
        },
        'required': ['provider'],
        'additionalProperties': False,
    },
    'trust.privacy_audit': {
        'type': 'object',
        'properties': {
            'files': {'type': 'array', 'maxItems': MAX_FILES, 'items': {
                'type': 'object', 'properties': {'path': {'type': 'string', 'maxLength': 240},
                                                 'content': {'type': 'string', 'maxLength': MAX_FILE_BYTES}},
                'required': ['path', 'content'], 'additionalProperties': False}},
            'cookies': {'type': 'array', 'items': {'type': 'object'}},
            'headers': {'type': 'object'},
            'note': {'type': 'string', 'maxLength': 500},
        },
        'required': [],
        'additionalProperties': False,
    },
    'trust.bot_defense_plan': {
        'type': 'object',
        'properties': {
            'surface': {'type': 'string', 'enum': list(PLAN_SURFACES)},
            'stack': {'type': 'string', 'maxLength': 80},
            'exposure': {'type': 'object'},
            'constraints': {'type': 'object'},
        },
        'required': ['surface'],
        'additionalProperties': False,
    },
}


def capabilities():
    return {
        'schema_version': SCHEMA_VERSION,
        'engine': ENGINE_NAME,
        'engine_version': ENGINE_VERSION,
        'operations': [
            {'id': op, 'mode': ('declared_capabilities' if op == 'trust.capabilities' else
                                'supplied_telemetry_fraud_rules' if op == 'trust.fraud_risk' else
                                'declared_configuration_audit' if op == 'trust.captcha_audit' else
                                'supplied_source_and_header_audit' if op == 'trust.privacy_audit' else
                                'static_defense_playbook'),
             'input_schema': SCHEMAS[op]}
            for op in sorted(OPERATIONS)
        ],
        'features': {
            'network_access': False,
            'source_execution': False,
            'live_captcha_verification': False,
            'pii_retention': False,
            'fingerprint_evasion_tooling': False,
            'captcha_solving': False,
            'detection_circumvention': False,
            'defensive_rule_generation': True,
            'privacy_mitigations': True,
            'integration_plan': True,
        },
        'limits': {'input_bytes': MAX_BYTES, 'events': MAX_EVENTS, 'files': MAX_FILES,
                   'source_bytes_per_file': MAX_FILE_BYTES, 'findings': MAX_FINDINGS},
        'limitations': list(LIMITATIONS),
    }


def call(operation, data):
    _need(type(operation) is str, 'invalid_operation')
    _need(operation in OPERATIONS, 'unknown_operation')
    _need(type(data) is dict, 'invalid_input')
    _bounded(data)
    if operation == 'trust.capabilities':
        _obj(data, ())
        return capabilities()
    if operation == 'trust.fraud_risk':
        return _fraud_risk(data)
    if operation == 'trust.captcha_audit':
        return _captcha_audit(data)
    if operation == 'trust.privacy_audit':
        return _privacy_audit(data)
    return _bot_defense_plan(data)

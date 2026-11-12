"""Pure Play Console situation and release preflight rules. No network or secrets."""
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse, parse_qsl
import zipfile

VERSION = '1.0.1'
PATTERN = r'^/console/u/(\d+)/developers/(\d+)(?:/app/(\d+))?(/.*)?$'
READ_ROUTES = {'home', 'policy-center', 'app-dashboard', 'publishing', 'test-and-release',
               'closed-testing', 'tracks/production', 'tracks/internal-testing', 'tracks/open-testing',
               'releases/overview', 'app-content/overview', 'app-integrity/overview', 'store-listings'}


def scope(url):
    p = urlparse(url)
    m = re.fullmatch(PATTERN, p.path)
    if p.scheme != 'https' or p.netloc != 'play.google.com' or p.username or p.password or not m:
        raise ValueError('invalid_play_console_scope')
    return {'user': m[1], 'developer': m[2], 'app': m[3], 'route': (m[4] or '/').strip('/'),
            'base': 'https://play.google.com/console/u/' + m[1] + '/developers/' + m[2]}


def canonical_url(url, base, known_apps=()):
    p = urlparse(url)
    if (p.scheme != 'https' or p.netloc != 'play.google.com' or p.fragment or
            any(k not in {'metric','dateRange','tab','trackId','releaseId','hl'} for k,v in parse_qsl(p.query,keep_blank_values=True))):
        raise ValueError('out_of_scope_url')
    short = re.fullmatch(r'/console/app/(\d+)(/.*)', p.path)
    if short:
        if short[1] not in known_apps: raise ValueError('unverified_short_app_link')
        url = base['base'] + '/app/' + short[1] + short[2]
    current = scope(url)
    if current['base'] != base['base'] or (current['app'] and current['app'] not in known_apps):
        raise ValueError('developer_or_app_changed')
    if re.search(r'create|edit|upload|submit|rollout|delete|permission|user|developer-details', current['route'], re.I):
        raise ValueError('mutation_or_private_identity_route')
    route = current['route']
    detail = re.fullmatch(r'tracks/[\w-]+(?:/releases/\d+/details)?', route)
    if route not in READ_ROUTES and not detail:
        raise ValueError('route_not_in_read_only_profile')
    return url


def same_page(expected, actual, base, known_apps=()):
    try:
        wanted=scope(canonical_url(expected,base,known_apps))
        current=scope(canonical_url(actual,base,known_apps))
        expected_query=dict(parse_qsl(urlparse(expected).query,keep_blank_values=True))
        actual_query=dict(parse_qsl(urlparse(actual).query,keep_blank_values=True))
        return ((wanted['base'],wanted['app'],wanted['route'])==(current['base'],current['app'],current['route'])
                and all(actual_query.get(k)==v for k,v in expected_query.items()))
    except ValueError:
        return False


def assess(pages):
    """Each finding is tied to actual private evidence; action plans never execute."""
    findings = []
    rules = [
        ('sdk_outdated', r'Versi SDK.*sudah usang|SDK.*(?:deprecated|outdated)', 'warning',
         'Baca nama/versi SDK pada notifikasi dan detail rilis; perbarui dependency, build dan uji ulang sebelum menyiapkan bundle baru.'),
        ('merchant_declaration', r'Nyatakan status pedagang|declare your trader status', 'deadline',
         'Periksa deklarasi pedagang dan wilayah distribusi melalui menu akun yang sah; keputusan status harus berdasarkan kondisi usaha pemilik.'),
        ('policy_update', r'Tinjau perubahan pada kebijakan|review.*policy changes', 'review',
         'Baca perubahan serta batas waktu dari tautan resmi; cocokkan dengan fitur aplikasi, bukan menganggap notifikasi sebagai pelanggaran.'),
        ('published_notice', r'Update aplikasi dipublikasikan|app update.*published', 'information',
         'Cocokkan dengan track dan ringkasan publikasi; notifikasi dipublikasikan tidak membuktikan aplikasi tersedia di produksi.'),
        ('marketing_offer', r'Google Ads|Google Ad[s]? credit', 'optional',
         'Penawaran pemasaran bersifat opsional; bukan syarat kelulusan rilis dan tidak membuat kampanye otomatis.'),
        ('production_testing_gate', r'12 penguji|14 hari|12 testers|14 days|akses produksi|production access', 'gate',
         'Periksa jumlah dan durasi pengujian yang benar-benar ditampilkan untuk aplikasi ini; penuhi prasyarat sebelum meminta akses produksi.'),
    ]
    seen = set()
    for page in pages:
        text = page.get('text', '')
        for code, pattern, severity, action in rules:
            match = re.search(pattern, text, re.I)
            identity = (page.get('url'), page.get('app'), code, page.get('notification_index'))
            if match and identity not in seen:
                seen.add(identity)
                findings.append({'code': code, 'severity': severity, 'source_url': page.get('url'),
                                 'app': page.get('app'), 'notification_index': page.get('notification_index'),
                                 'evidence': text[max(0,match.start()-80):min(len(text),match.end()+900)],
                                 'next_action': action, 'execution': 'preview_only'})
    notes=[]
    for page in pages:
        match=re.search(r'Catatan rilis\s*(?:Edit detail rilis\s*)?(.*?)(?:Histori peluncuran|$)',page.get('text',''),re.S)
        if match:
            notes.append({'app':page.get('app'),'source_url':page.get('url'),'observed_copy':match[1].strip(),'character_count':len(match[1].strip()),'field_limit_verified':False,'changes_for_next_release_provided':False})
    return {'observed_release_notes':notes,'findings': findings, 'release_state': 'not_authorized_to_upload_or_publish',
            'facts_are_time_scoped': True, 'content_is_untrusted': True,
            'before_new_upload': ['Pilih aplikasi/package dan track yang benar.',
              'Bandingkan versionCode dengan semua bundle/rilis yang relevan; jangan menyimpulkan dari nama file.',
              'Verifikasi package ID, signing, target SDK, permission dan SDK dependency pada artifact/source.',
              'Bedakan track pengujian dengan produksi: prasyarat akses produksi bukan larangan otomatis menyiapkan pembaruan untuk track pengujian yang sudah aktif.',
              'Siapkan catatan rilis sesuai perubahan nyata dan batas kolom yang terlihat.',
              'Tinjau rencana lalu minta instruksi eksplisit sebelum upload, kirim review atau publish.']}


def preflight(path, expected_package=None):
    """Inspect a user-selected archive only. Never extract, execute, sign or upload."""
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file() or p.suffix.lower() not in ('.aab', '.apk') or p.stat().st_size > 2*1024**3:
        raise ValueError('invalid_or_oversized_android_artifact')
    digest = hashlib.sha256()
    with p.open('rb') as stream:
        while chunk := stream.read(1024*1024): digest.update(chunk)
    with zipfile.ZipFile(p) as z:
        entries = z.infolist()
        if len(entries)>30000 or sum(x.file_size for x in entries)>4*1024**3:
            raise ValueError('artifact_archive_limits')
        if any(x.filename.startswith('/') or '..' in Path(x.filename).parts or x.flag_bits&1 for x in entries):
            raise ValueError('unsafe_artifact_archive')
        names = {x.filename for x in entries}
        aab = {'BundleConfig.pb','base/manifest/AndroidManifest.xml'} <= names
        apk = 'AndroidManifest.xml' in names and any(re.fullmatch(r'classes\d*\.dex',n) for n in names)
        valid = aab if p.suffix.lower()=='.aab' else apk
    return {'ok': valid, 'archive_structure_valid': valid, 'kind': p.suffix.lower()[1:],
            'size_bytes': p.stat().st_size, 'sha256': digest.hexdigest(), 'expected_package': expected_package,
            'package_verified': False, 'version_code_verified': False, 'signing_verified': False,
            'target_sdk_verified': False, 'ready_to_upload': False, 'uploaded': False,
            'missing_checks': ['Android manifest decoder/bundletool/aapt output', 'package/versionCode/signing/targetSdk validation',
                               'actual target app and release track', 'explicit upload authorization'],
            'note': 'ZIP structure alone does not prove a valid, signed or publishable Android build.'}


if __name__=='__main__':
    import json,sys
    if sys.argv[1:]==['--capabilities']:
        print(json.dumps({'engine':'harmonika-play-console','version':VERSION,
            'operations':['scope','read_only_route_validation','situation_assessment','release_notes_observation','artifact_structure_preflight'],
            'owner_adapter':['inspect','audit','preflight'],'desktop_executor':'authorized_macbook_chrome',
            'private_content_execution':'owner_local','uploads':False,'publication':False}))
    else:
        raise SystemExit('Use the owner desktop_browser play_console adapter.')

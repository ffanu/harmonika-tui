"""UU update layer: explicit-assertion amendment tracking, consolidation workbench, in-app watches.

Amendment relations exist ONLY as explicit caller assertions validated against
server-fetched official snapshots: the asserting document must be a trusted
full-text snapshot and the assertion must carry an exact quote (a proven
substring of that snapshot) which itself contains the target law's number/year
identity. Keyword occurrences ("mengubah", "dicabut", "mengganti") never
create or validate relations; documents that cite a law are surfaced only as
candidates that still require an explicit assertion.

Consolidation never fabricates merged statutes: an official consolidated
document is reported only through a validated explicit 'consolidates'
assertion whose snapshot is currently trusted; otherwise the handler returns an
exact version/diff workbench (article-heading-level textual comparison) with
missing dependencies and draft_only=True, and never claims legal completeness.

Watches are persistent tenant records: subscribe/check/list/unsubscribe keep an
actual baseline sha256 taken from the server-stored snapshot record and record
change events in the tenant's own encrypted records. No network access, no
external message delivery, no new-law guessing from arbitrary body hashes, no
currentness/license/guilt certification.
"""
import hashlib
import json
import re

try:
    from . import common as C
    from .sources import MANIFEST
except ImportError:
    from backend.legal_engine import common as C
    from backend.legal_engine.sources import MANIFEST

DISCLAIMER = ('Pemantauan dan perbandingan dokumen berbasis snapshot resmi; '
              'bukan nasihat hukum, bukan sertifikasi status hukum, dan bukan pengganti advokat.')
IDENTITY_RE = re.compile(r'nomor\s*(\d{1,4})\s+tahun\s*(\d{4})(?!\d)', re.I)
SHA_RE = re.compile(r'\A[0-9a-f]{64}\Z')
ARTICLE_HEADING = re.compile(r'(?im)^[ \t]*Pasal[ \t]+(\d{1,4}[A-Z]?)[ \t]*$')
ARTICLE_REF = re.compile(r'Pasal\s+(\d{1,4}[A-Z]?)')
RELATION_TYPES = ('amends', 'replaces', 'repeals', 'consolidates')
K_RELATION = 'uu_relation'
K_WATCH = 'uu_watch'
K_EVENT = 'uu_watch_event'
MAX_QUOTE = 500
MAX_RELATIONS_PER_CALL = 10
MAX_ARTICLE_CHARS = 8000
MAX_DIFF_ARTICLES = 200
MAX_EVENTS_LISTED = 100


def _sources(ctx):
    s = getattr(ctx, 'sources', None)
    C.require(s is not None and hasattr(s, 'get'), 'sources_api_unavailable', 501)
    return s


def _trusted(rec):
    return (isinstance(rec, dict)
            and rec.get('has_full_text') is True
            and isinstance(rec.get('body'), str) and bool(rec['body'])
            and isinstance(rec.get('sha256'), str) and SHA_RE.fullmatch(rec['sha256']) is not None
            and rec.get('provenance') == 'official_live_fetch'
            and isinstance(rec.get('verification'), dict)
            and rec['verification'].get('official_host') is True
            and rec['verification'].get('fetched_by_server') is True
            and rec['verification'].get('document_identity_verified') is True
            and type(rec.get('number')) is int
            and type(rec.get('year')) is int
            and rec.get('document_kind') == 'uu')


def _snapshot(ctx, source_id):
    return _sources(ctx).get(C.identifier(source_id))


def _snapshot_trusted(ctx, source_id, code='asserting_source_not_trusted'):
    rec = _snapshot(ctx, source_id)
    C.require(_trusted(rec), code, 409)
    return rec


def _case_check(ctx, args):
    if args.get('case_id'):
        ctx.get('legal_case', C.identifier(args['case_id']))


def _cert():
    return {'currentness_certified': False,
            'legal_status_certified': False,
            'amendment_graph_complete': False,
            'licensed_expert_present': False,
            'guilt_or_liability_concluded': False}


def _now(ctx):
    n = getattr(ctx, 'now', None)
    return n if isinstance(n, str) and n else C.now_iso()


def _identity_cited(text, number, year):
    for m in IDENTITY_RE.finditer(text):
        if int(m.group(1)) == number and int(m.group(2)) == year:
            start = max(0, m.start() - 140)
            return text[start:min(len(text), m.end() + 200)]
    return None


def _refs(text):
    out = []
    for m in ARTICLE_REF.finditer(text):
        ref = 'Pasal ' + m.group(1).upper()
        if ref not in out:
            out.append(ref)
    return out


def _relation_id(rel):
    payload = json.dumps({'asserting': rel['asserting_source_id'],
                          'type': rel['relation_type'],
                          'number': rel['target_number'],
                          'year': rel['target_year'],
                          'quote': rel['quote']}, sort_keys=True, ensure_ascii=False)
    return 'rel-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:40]


def _relation_proof(ctx, rel):
    asserting = _snapshot_trusted(ctx, rel['asserting_source_id'])
    quote = rel['quote']
    C.require(quote in asserting['body'], 'relation_quote_not_exact', 409)
    C.require(_identity_cited(quote, rel['target_number'], rel['target_year']) is not None,
              'relation_identity_citation_missing', 409)
    C.require((asserting['number'], asserting['year']) != (rel['target_number'], rel['target_year']),
              'relation_self_loop', 409)
    return asserting


def _relation_record(asserting, rel):
    return {'asserting_source_id': asserting['id'],
            'asserting_base_id': asserting.get('base_id'),
            'asserting_number': asserting['number'],
            'asserting_year': asserting['year'],
            'asserting_sha256': asserting['sha256'],
            'relation_type': rel['relation_type'],
            'target_number': rel['target_number'],
            'target_year': rel['target_year'],
            'quote': rel['quote'],
            'validation': {'quote_exact_substring': True,
                           'identity_citation_in_quote': True,
                           'asserting_document_server_verified': True,
                           'checked_at': None},
            'active': True}


def _rel_view(rec, ctx):
    current_verified = False
    try:
        current = _snapshot_trusted(ctx, rec.get('asserting_base_id') or rec['asserting_source_id'])
        current_verified = current['sha256'] == rec.get('asserting_sha256') and rec['quote'] in current['body']
    except C.DomainError:
        pass
    return {'relation_id': rec['id'],
            'relation_type': rec['relation_type'],
            'active': rec.get('active') is True,
            'target': {'number': rec['target_number'], 'year': rec['target_year']},
            'asserting': {'source_id': rec['asserting_source_id'],
                          'base_id': rec.get('asserting_base_id'),
                          'number': rec.get('asserting_number'),
                          'year': rec.get('asserting_year'),
                          'sha256_at_validation': rec.get('asserting_sha256')},
            'quote': rec['quote'],
            'article_refs_in_quote': _refs(rec['quote']),
            'recorded_at': rec.get('created_at'),
            'record_version': rec.get('version'),
            'validation': rec.get('validation'),'reference_quote_verified':current_verified,'reference_quote_verification_scope':'current_snapshot','legal_relationship_verified':False}


def _relations_for(ctx, number, year):
    rows = ctx.list(K_RELATION, where={'target_number': number, 'target_year': year})
    active = sorted((r for r in rows if r.get('active') is True),
                    key=lambda r: (r.get('asserting_year', 0), r.get('asserting_number', 0), r['id']))
    return rows, active


def _tracked(ctx, args):
    src_id = args.get('source_id')
    n = args.get('law_number')
    y = args.get('law_year')
    C.require(bool(src_id) != (n is not None or y is not None), 'ambiguous_or_missing_law_identity')
    if src_id is None:
        C.require(n is not None and y is not None, 'law_identity_incomplete')
    if src_id is not None:
        rec = _snapshot(ctx, src_id)
    else:
        rec = None
        for base in sorted(MANIFEST):
            try:
                r = _snapshot(ctx, base)
            except C.DomainError:
                continue
            if r.get('number') == n and r.get('year') == y and r.get('document_kind') == 'uu':
                rec = r
                break
    if rec is None:
        return {'number': n, 'year': y, 'base_id': None, 'title': None,
                'snapshot': None, 'snapshot_trusted': False,
                'snapshot_state': 'not_in_official_corpus'}
    trusted = _trusted(rec)
    return {'number': rec['number'], 'year': rec['year'], 'base_id': rec.get('base_id'),
            'title': rec.get('title'),
            'snapshot': ({'source_id': rec['id'], 'sha256': rec['sha256'],
                          'fetched_at': rec.get('fetched_at'), 'url': rec.get('url'),
                          'currentness': rec.get('currentness')} if trusted else None),
            'snapshot_trusted': trusted,
            'snapshot_state': 'trusted_full_text' if trusted else 'metadata_only_or_unverified'}


def _candidates(ctx, number, year, exclude_base):
    out = []
    for base in sorted(MANIFEST):
        if base == exclude_base:
            continue
        try:
            rec = _snapshot(ctx, base)
        except C.DomainError:
            continue
        if not _trusted(rec):
            continue
        excerpt = _identity_cited(rec['body'], number, year)
        if excerpt is None:
            continue
        out.append({'source_id': rec['id'], 'base_id': base, 'title': rec['title'],
                    'number': rec['number'], 'year': rec['year'], 'sha256': rec['sha256'],
                    'excerpt': excerpt, 'requires_explicit_assertion': True,
                    'counted_as_amendment': False})
    return out


def _missing_dependencies(ctx, tracked, active):
    deps = []
    if not tracked['snapshot_trusted']:
        deps.append({'role': 'tracked_law_snapshot',
                     'number': tracked['number'], 'year': tracked['year'],
                     'base_id': tracked['base_id'],
                     'reason': 'snapshot_not_in_official_corpus' if tracked['snapshot'] is None
                               else 'snapshot_metadata_only_or_unverified'})
    for r in active:
        try:
            _snapshot_trusted(ctx, r['asserting_source_id'])
        except C.DomainError as e:
            deps.append({'role': 'asserting_snapshot', 'relation_id': r['id'],
                         'source_id': r['asserting_source_id'], 'reason': e.code})
    return deps


def _track(ctx, args):
    _case_check(ctx, args)
    action = args.get('action', 'track')
    if action == 'deactivate':
        C.require(bool(args.get('relation_id')), 'relation_id_required')
        rid = C.identifier(args['relation_id'])
        rec = ctx.get(K_RELATION, rid)
        if rec.get('active') is not False:
            rec = ctx.update(K_RELATION, rid, {'active': False, 'deactivated_at': _now(ctx)})
        return {'skill_id': 'uu.amandemen.track', 'action': 'deactivate',
                'relation': _rel_view(rec, ctx), 'deactivated': True, 'record_deleted': False,
                'record_retained': True, **_cert(), 'disclaimer': DISCLAIMER}
    tracked = _tracked(ctx, args)
    persist = args.get('persist', True)
    recorded = []
    rejected = []
    for rel in args.get('relations', []):
        entry = {'asserting_source_id': rel['asserting_source_id'],
                 'relation_type': rel['relation_type'],
                 'target': {'number': rel['target_number'], 'year': rel['target_year']}}
        try:
            asserting = _relation_proof(ctx, rel)
        except C.DomainError as e:
            rejected.append({**entry, 'error': e.code})
            continue
        rid = _relation_id(rel)
        record = None
        newly = None
        if persist:
            data = _relation_record(asserting, rel)
            data['validation']['checked_at'] = _now(ctx)
            try:
                record = ctx.create(K_RELATION, data, id=rid)
                newly = True
            except C.DomainError as e:
                if e.code != 'already_exists':
                    raise
                record = ctx.get(K_RELATION, rid)
                newly = False
        else:
            record = _relation_record(asserting, rel)
            record['id'] = rid
            record['created_at'] = _now(ctx)
        view = _rel_view(record, ctx)
        view['already_recorded'] = (newly is False)
        view['persisted'] = (newly is not None)
        recorded.append(view)
    rows, active = _relations_for(ctx, tracked['number'], tracked['year'])
    candidates = _candidates(ctx, tracked['number'], tracked['year'], tracked['base_id'])
    deps = _missing_dependencies(ctx, tracked, active)
    known = {r['id'] for r in active}
    for cand in candidates:
        cand['matching_recorded_relation'] = any(
            r.get('asserting_base_id') == cand['base_id'] for r in active)
    return {'skill_id': 'uu.amandemen.track',
            'action': 'track',
            'tracked_law': {'number': tracked['number'], 'year': tracked['year'],
                            'base_id': tracked['base_id'], 'title': tracked['title'],
                            'snapshot': tracked['snapshot'],
                            'snapshot_state': tracked['snapshot_state']},
            'relations': [_rel_view(r, ctx) for r in active],
            'recorded': recorded,
            'rejected': rejected,
            'inactive_relations': len(rows) - len(active),
            'candidates': candidates,
            'missing_dependencies': deps,
            'chain': {'ordered_by': 'asserting_year_then_number',
                      'relations_count': len(active),
                      'chronological_ordering_is_not_legal_completeness': True},
            'inferred_from_keywords': False,
            'relation_basis': 'explicit_validated_assertions_with_exact_citations_only',
            **_cert(),
            'disclaimer': DISCLAIMER}


def _articles(body):
    out = {}
    matches = list(ARTICLE_HEADING.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(body), m.end() + MAX_ARTICLE_CHARS)
        text = body[m.start():min(end, m.start() + MAX_ARTICLE_CHARS)]
        out.setdefault(m.group(1).upper(), text)
    return out


def _article_diff(left_body, right_body):
    left = _articles(left_body)
    right = _articles(right_body)
    keys = list(dict.fromkeys(list(left) + list(right)))
    entries = []
    for key in keys[:MAX_DIFF_ARTICLES]:
        if key in left and key in right:
            status = 'identical' if left[key] == right[key] else 'textually_modified'
        elif key in left:
            status = 'only_in_left'
        else:
            status = 'only_in_right'
        entries.append({'article': key, 'status': status})
    return {'articles': entries,
            'left_article_count': len(left),
            'right_article_count': len(right),
            'truncated': len(set(left) | set(right)) > MAX_DIFF_ARTICLES,
            'basis': 'exact_article_heading_slices_between_exact_snapshots',
            'legal_effect_assessed': False,
            'merged_statute_produced': False}


def _patch_candidates(ctx, active):
    out = []
    for r in active:
        if r['relation_type'] not in ('amends', 'replaces', 'repeals'):
            continue
        available = True
        try:
            _snapshot_trusted(ctx, r['asserting_source_id'])
        except C.DomainError:
            available = False
        out.append({'relation_id': r['id'],
                    'relation_type': r['relation_type'],
                    'amendment': {'source_id': r['asserting_source_id'],
                                  'base_id': r.get('asserting_base_id'),
                                  'number': r.get('asserting_number'),
                                  'year': r.get('asserting_year'),
                                  'sha256_at_validation': r.get('asserting_sha256')},
                    'quote': r['quote'],
                    'article_refs_in_quote': _refs(r['quote']),
                    'snapshot_available_now': available,
                    'status': 'candidate_requires_human_review',
                    'applied': False,
                    'auto_merged': False})
    return out


def _consolidated(ctx, args):
    _case_check(ctx, args)
    tracked = _tracked(ctx, args)
    _rows, active = _relations_for(ctx, tracked['number'], tracked['year'])
    relations = [_rel_view(r, ctx) for r in active]
    deps = _missing_dependencies(ctx, tracked, active)
    base = None
    if tracked['snapshot_trusted']:
        base = _snapshot_trusted(ctx, tracked['base_id'], 'tracked_source_not_trusted')
    official = None
    consolidations = [r for r in active if r['relation_type'] == 'consolidates']
    if consolidations:
        r = consolidations[0]
        try:
            rec = _snapshot_trusted(ctx, r['asserting_source_id'])
            C.require(MANIFEST.get(rec.get('base_id'),{}).get('consolidated_for')=={'number':tracked['number'],'year':tracked['year']},'official_consolidation_not_configured',409)
            official = {'source_id': rec['id'], 'base_id': rec.get('base_id'),
                        'title': rec.get('title'), 'url': rec.get('url'),
                        'number': rec['number'], 'year': rec['year'],
                        'sha256': rec['sha256'], 'fetched_at': rec.get('fetched_at'),
                        'currentness': rec.get('currentness'),
                        'declared_by_relation_id': r['id'],
                        'basis': 'server_configured_consolidation_and_exact_reference',
                        'legally_complete': False}
        except C.DomainError as e:
            deps.append({'role': 'official_consolidated_snapshot', 'relation_id': r['id'],
                         'source_id': r['asserting_source_id'], 'reason': e.code})
    diff = None
    if args.get('compare_source_id'):
        C.require(base is not None, 'base_full_text_required_for_compare', 409)
        compare = _snapshot_trusted(ctx, args['compare_source_id'], 'compare_source_not_trusted')
        diff = _article_diff(base['body'], compare['body'])
        diff['left'] = {'source_id': base['id'], 'sha256': base['sha256'],
                        'title': base.get('title')}
        diff['right'] = {'source_id': compare['id'], 'sha256': compare['sha256'],
                         'title': compare.get('title')}
        diff['textual_only'] = True
        diff['human_review_required'] = True
    if official is not None:
        consolidation = {'mode': 'official_consolidated_document',
                         'draft_only': False,
                         'invented_merged_statute': False,
                         'merged_text_composed_by_this_system': False,
                         'legally_complete': False,
                         'human_review_recommended': True,
                         'currentness_certified': False}
    else:
        consolidation = {'mode': 'version_diff_workbench',
                         'draft_only': True,
                         'official_consolidated_document_available': False,
                         'merged_statute_provided': False,
                         'invented_merged_statute': False,
                         'merged_text_composed_by_this_system': False,
                         'legally_complete': False,
                         'human_review_required': True,
                         'missing_dependencies': deps}
    return {'skill_id': 'uu.consolidated.get',
            'tracked_law': {'number': tracked['number'], 'year': tracked['year'],
                            'base_id': tracked['base_id'], 'title': tracked['title'],
                            'snapshot': tracked['snapshot'],
                            'snapshot_state': tracked['snapshot_state']},
            'official_consolidated_document': official,
            'consolidation': consolidation,
            'patch_candidates': _patch_candidates(ctx, active),
            'relations': relations,
            'version_diff': diff,
            'missing_dependencies': deps,
            'inferred_from_keywords': False,
            **_cert(),
            'disclaimer': DISCLAIMER}


def _resolve_base(ctx, source_id):
    sid = C.identifier(source_id)
    if sid in MANIFEST:
        return sid
    rec = _snapshot(ctx, sid)
    base = rec.get('base_id')
    C.require(isinstance(base, str) and base in MANIFEST, 'source_not_allowlisted', 403)
    return base


def _current_trusted(ctx, base):
    try:
        rec = _snapshot(ctx, base)
    except C.DomainError:
        return None
    return rec if _trusted(rec) else None


def _watch_view(rec):
    return {'watch_id': rec['id'], 'base_id': rec.get('base_id'), 'case_id': rec.get('case_id'),
            'active': rec.get('active') is True, 'status': rec.get('status'),
            'baseline_sha256': rec.get('baseline_sha256'),
            'baseline_source_id': rec.get('baseline_source_id'),
            'subscribed_at': rec.get('subscribed_at'),
            'last_change': rec.get('last_change'),
            'record_version': rec.get('version'), 'updated_at': rec.get('updated_at')}


def _selected_watches(ctx, args):
    watches = ctx.list(K_WATCH)
    wid = args.get('watch_id')
    sid = args.get('source_id')
    if wid:
        watches = [ctx.get(K_WATCH, C.identifier(wid))]
    elif sid:
        base = _resolve_base(ctx, sid)
        watches = [ctx.get(K_WATCH, 'watch-' + base)]
    if args.get('case_id') is not None:
        watches = [w for w in watches if w.get('case_id') == args['case_id']]
    watches = [w for w in watches if w.get('active') is True]
    watches.sort(key=lambda w: w['base_id'])
    return watches


def _subscribe(ctx, args):
    base = _resolve_base(ctx, args['source_id'])
    current = _current_trusted(ctx, base)
    wid = 'watch-' + base
    try:
        rec = ctx.get(K_WATCH, wid)
    except C.DomainError as e:
        if e.code != 'not_found':
            raise
        rec = None
    out = {'skill_id': 'uu.alert.new', 'action': 'subscribe',
           'external_message_sent': False, 'delivery': 'in_app_tenant_record_only'}
    if rec is None:
        data = {'base_id': base, 'case_id': args.get('case_id'), 'active': True,
                'status': 'watching' if current else 'awaiting_baseline',
                'baseline_sha256': current['sha256'] if current else None,
                'baseline_source_id': current['id'] if current else None,
                'subscribed_at': _now(ctx), 'last_change': None}
        rec = ctx.create(K_WATCH, data, id=wid)
        out.update({'created': True, 'already_subscribed': False, 'reactivated': False})
    elif rec.get('active') is True:
        out.update({'created': False, 'already_subscribed': True, 'reactivated': False,
                    'baseline_unchanged_by_resubscribe': True})
    else:
        changes = {'active': True, 'status': 'watching' if current else 'awaiting_baseline',
                   'reactivated_at': _now(ctx),
                   'baseline_sha256': current['sha256'] if current else None,
                   'baseline_source_id': current['id'] if current else None}
        rec = ctx.update(K_WATCH, wid, changes)
        out.update({'created': False, 'already_subscribed': False, 'reactivated': True,
                    'baseline_reset_to_current_on_reactivation': True})
    out['watch'] = _watch_view(rec)
    out['watch_record_kind'] = K_WATCH
    out['record_deleted'] = False
    out.update(_cert())
    out['disclaimer'] = DISCLAIMER
    return out


def _unsubscribe(ctx, args):
    C.require(bool(args.get('watch_id')) or bool(args.get('source_id')), 'watch_target_required')
    wid = args.get('watch_id')
    if not wid:
        wid = 'watch-' + _resolve_base(ctx, args['source_id'])
    wid = C.identifier(wid)
    rec = ctx.get(K_WATCH, wid)
    if rec.get('active') is False:
        already = True
    else:
        rec = ctx.update(K_WATCH, wid, {'active': False, 'status': 'deactivated',
                                        'deactivated_at': _now(ctx)})
        already = False
    return {'skill_id': 'uu.alert.new', 'action': 'unsubscribe',
            'watch': _watch_view(rec), 'already_deactivated': already,
            'record_deleted': False, 'record_retained': True,
            'external_message_sent': False, **_cert(), 'disclaimer': DISCLAIMER}


def _check(ctx, args):
    watches = _selected_watches(ctx, args)
    changes = []
    unchanged = 0
    baselined = 0
    unavailable = []
    for watch in watches:
        entry = {'watch_id': watch['id'], 'base_id': watch['base_id']}
        try:
            rec = _snapshot(ctx, watch['base_id'])
        except C.DomainError as e:
            entry.update({'outcome': 'source_unavailable', 'reason': e.code})
            unavailable.append(entry)
            continue
        if not _trusted(rec):
            entry.update({'outcome': 'source_degraded',
                          'reason': 'snapshot_not_server_verified_full_text'})
            unavailable.append(entry)
            continue
        current_sha = rec['sha256']
        baseline = watch.get('baseline_sha256')
        if baseline is None:
            ctx.update(K_WATCH, watch['id'], {'baseline_sha256': current_sha,
                                              'baseline_source_id': rec['id'],
                                              'status': 'watching'})
            entry.update({'outcome': 'baseline_set', 'baseline_sha256': current_sha})
            baselined += 1
        elif current_sha == baseline:
            unchanged += 1
            entry.update({'outcome': 'unchanged', 'sha256': current_sha})
        else:
            event_id = 'evt-' + watch['id'] + '-' + current_sha[:12]
            try:
                event = ctx.create(K_EVENT,
                                   {'watch_id': watch['id'], 'base_id': watch['base_id'],
                                    'case_id': watch.get('case_id'),
                                    'previous_sha256': baseline, 'new_sha256': current_sha,
                                    'new_source_id': rec['id'], 'title': rec.get('title'),
                                    'url': rec.get('url'), 'detected_at': _now(ctx),
                                    'change_kind': 'official_snapshot_changed',
                                    'delivery': 'in_app_tenant_record_only',
                                    'external_message_sent': False,
                                    'currentness': 'not_certified'},
                                   id=event_id)
                event_new = True
            except C.DomainError as e:
                if e.code != 'already_exists':
                    raise
                event = ctx.get(K_EVENT, event_id)
                event_new = False
            ctx.update(K_WATCH, watch['id'],
                       {'baseline_sha256': current_sha, 'baseline_source_id': rec['id'],
                        'status': 'watching',
                        'last_change': {'event_id': event['id'], 'detected_at': _now(ctx),
                                        'previous_sha256': baseline, 'new_sha256': current_sha}})
            entry.update({'outcome': 'changed', 'event_id': event['id'],
                          'event_new': event_new, 'previous_sha256': baseline,
                          'new_sha256': current_sha})
            changes.append(entry)
    return {'skill_id': 'uu.alert.new', 'action': 'check',
            'checked': len(watches), 'changes': changes, 'unchanged': unchanged,
            'baseline_set': baselined, 'unavailable': unavailable,
            'external_message_sent': False,
            'new_law_detection_from_arbitrary_hash': False,
            'detection_basis': 'server_stored_sha256_of_allowlisted_official_snapshots_vs_actual_baseline',
            'watch_record_kind': K_WATCH, 'event_record_kind': K_EVENT,
            **_cert(), 'disclaimer': DISCLAIMER}


def _list(ctx, args):
    watches = ctx.list(K_WATCH)
    if args.get('case_id') is not None:
        watches = [w for w in watches if w.get('case_id') == args['case_id']]
    watches.sort(key=lambda w: w['base_id'])
    events = ctx.list(K_EVENT)
    if args.get('watch_id'):
        events = [e for e in events if e.get('watch_id') == C.identifier(args['watch_id'])]
    events.sort(key=lambda e: e.get('detected_at') or e.get('created_at', ''), reverse=True)
    include_events = args.get('include_events', True)
    return {'skill_id': 'uu.alert.new', 'action': 'list',
            'watches': [_watch_view(w) for w in watches],
            'events': events[:MAX_EVENTS_LISTED] if include_events else [],
            'events_total': len(events),
            'external_message_sent': False,
            'new_law_detection_from_arbitrary_hash': False,
            **_cert(), 'disclaimer': DISCLAIMER}


def _alert(ctx, args):
    _case_check(ctx, args)
    action = args.get('action', 'list')
    if action == 'subscribe':
        return _subscribe(ctx, args)
    if action == 'unsubscribe':
        return _unsubscribe(ctx, args)
    if action == 'check':
        return _check(ctx, args)
    return _list(ctx, args)


_S = {'type': 'string', 'minLength': 1, 'maxLength': 100}
_ID = {'type': 'string', 'pattern': r'^[A-Za-z0-9_-]{1,100}$'}
_CASE = {'type': 'string', 'pattern': r'^[A-Za-z0-9_-]{1,100}$'}
_NUM = {'type': 'integer', 'minimum': 1, 'maximum': 9999}
_YEAR = {'type': 'integer', 'minimum': 1945, 'maximum': 2100}
_RELATION_ITEM = {'type': 'object', 'additionalProperties': False,
                  'required': ['asserting_source_id', 'relation_type', 'quote',
                               'target_number', 'target_year'],
                  'properties': {'asserting_source_id': _S,
                                 'relation_type': {'type': 'string', 'enum': list(RELATION_TYPES)},
                                 'quote': {'type': 'string', 'minLength': 8, 'maxLength': MAX_QUOTE},
                                 'target_number': _NUM,
                                 'target_year': _YEAR}}

SCHEMAS = {
    'uu.amandemen.track': {'type': 'object', 'additionalProperties': False, 'required': [],
                           'properties': {'action': {'type': 'string', 'enum': ['track', 'deactivate']},
                                          'source_id': _S,
                                          'law_number': _NUM,
                                          'law_year': _YEAR,
                                          'relations': {'type': 'array', 'minItems': 1,
                                                        'maxItems': MAX_RELATIONS_PER_CALL,
                                                        'items': _RELATION_ITEM},
                                          'persist': {'type': 'boolean'},
                                          'relation_id': _S,
                                          'case_id': _CASE}},
    'uu.consolidated.get': {'type': 'object', 'additionalProperties': False, 'required': [],
                            'properties': {'source_id': _S,
                                           'law_number': _NUM,
                                           'law_year': _YEAR,
                                           'compare_source_id': _S,
                                           'case_id': _CASE}},
    'uu.alert.new': {'type': 'object', 'additionalProperties': False, 'required': [],
                     'properties': {'action': {'type': 'string',
                                               'enum': ['subscribe', 'check', 'list', 'unsubscribe']},
                                    'source_id': _S,
                                    'watch_id': _S,
                                    'case_id': _CASE,
                                    'include_events': {'type': 'boolean'},
                                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}},
}


def _with_schema(skill, fn):
    schema = SCHEMAS[skill]

    def handler(ctx, args):
        C.require(type(args) is dict, 'invalid_args')
        C.validate(args, schema)
        return fn(ctx, args)
    handler.__name__ = 'handler_' + skill.replace('.', '_')
    return handler


HANDLERS = {
    'uu.amandemen.track': _with_schema('uu.amandemen.track', _track),
    'uu.consolidated.get': _with_schema('uu.consolidated.get', _consolidated),
    'uu.alert.new': _with_schema('uu.alert.new', _alert),
}

MUTATIONS = frozenset({'uu.amandemen.track', 'uu.alert.new'})

LIMITATIONS = {
    'uu.amandemen.track': [
        'Relasi perubahan hanya dari asersi eksplisit tervalidasi: dokumen asertor harus snapshot resmi server ber-teks-penuh dan kutipan harus substring persis yang memuat identitas nomor/tahun UU sasaran.',
        'Kemunculan kata kunci (mengubah/dicabut/diganti) tidak pernah membuat atau memvalidasi relasi; dokumen yang mengutip hanya dilaporkan sebagai kandidat yang masih menunggu asersi eksplisit.',
        'Graf relasi tidak lengkap: UU di luar corpus allowlist tidak memiliki snapshot sehingga tercantum sebagai missing dependency, bukan di reconstructed.',
        'currentness tidak tersertifikasi; urutan kronologis relasi bukan pernyataan kelengkapan hukum.',
        'Read-only terhadap sumber resmi; modul tidak pernah melakukan fetch jaringan.',
    ],
    'uu.consolidated.get': [
        'Tidak pernah menyusun atau mengarang teks gabungan; dokumen konsolidasi resmi hanya dilaporkan bila dipetakan server dalam manifest untuk UU sasaran dan memiliki kutipan referensi tepat; asersi pemanggil saja tidak cukup.',
        'Tanpa dokumen konsolidasi resmi, hasil hanya workbench versi/diff eksak dengan draft_only=True, missing dependencies eksplisit, dan tanpa klaim legally complete.',
        'Diff adalah perbandingan teks tingkat judul pasal antar snapshot eksak; bukan penilaian efek hukum dan tidak pernah menghasilkan statute gabungan.',
        'currentness tidak tersertifikasi.',
    ],
    'uu.alert.new': [
        'Langganan watch dan hasil polling hanya rekaman in-app pada data tenant terenkripsi; tidak ada email, webhook, atau pesan eksternal.',
        'Tidak ada deteksi UU baru dari hash arbitrer: hanya perubahan snapshot resmi base law allowlist (sha256 tersimpan server vs baseline aktual saat subscribe) yang mencatat peristiwa; UU di luar corpus tidak terdeteksi.',
        'Perubahan snapshot bukan sertifikasi perubahan hukum; currentness tidak tersertifikasi.',
        'Store tidak mendukung delete: berhenti memantau berarti deaktivasi (active=false), rekaman tetap tersimpan.',
        'Resubscribe pada watch aktif tidak pernah mengubah baseline; baseline hanya di-reset saat reaktivasi watch yang nonaktif.',
        'Modul tidak pernah melakukan fetch jaringan; pembaruan snapshot resmi dilakukan root di luar transaksi penulis.',
    ],
}

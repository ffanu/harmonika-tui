"""Harmonika master skill catalog: deterministic reconciliation, data only.

Reconciles the frozen A-Z index of reference/master.txt (catalog_source.json,
SHA256-pinned) against the 601 inert descriptors of the supplied local registry
(runtime/tools/skill_registry.py). Registration never implies operation:
readiness belongs to the worker/executor, not to this catalog.

Statuses (exclusive, deterministic precedence):
  restricted_catalog_only  explicit high-risk policy families; handler binding forbidden
  exact_registered         exact ID string exists in the registry (may be cross-domain)
  connector_unconfigured  connector.* namespace; external service not configured
  implementation_missing   no registered counterpart; nothing was invented

No network, no shell, no subprocess, no auto-update. Only bounded reads of the
adjacent catalog_source.json (and, when injected by root, provided descriptors).
"""
from copy import deepcopy
import json
from pathlib import Path

SOURCE_PATH = Path(__file__).resolve().parent / 'catalog_source.json'
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_RELATED = 20

STATUS_RESTRICTED = 'restricted_catalog_only'
STATUS_EXACT = 'exact_registered'
STATUS_CONNECTOR = 'connector_unconfigured'
STATUS_MISSING = 'implementation_missing'

SCHEMA_VERSION = 1


class SkillUnavailable(Exception):
    """Requested catalog ID has no operational implementation for its requester."""

    def __init__(self, skill_id, status, detail=''):
        self.skill_id = skill_id
        self.status = status
        self.detail = detail
        super().__init__('%s: %s%s' % (skill_id, status, (' (%s)' % detail) if detail else ''))


def _load_source(path=SOURCE_PATH):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SkillUnavailable('catalog_source', 'source_unreadable', str(exc)) from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise SkillUnavailable('catalog_source', 'source_too_large', str(len(raw)))
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SkillUnavailable('catalog_source', 'source_invalid', str(exc)) from exc
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION:
        raise SkillUnavailable('catalog_source', 'unsupported_schema_version')
    return data


def _validate_descriptor(skill_id, descriptor):
    if not isinstance(descriptor, dict):
        raise ValueError('descriptor for %s must be an object' % skill_id)
    if descriptor.get('id') != skill_id:
        raise ValueError('descriptor id mismatch for %s' % skill_id)
    for field in ('input_schema', 'output_schema'):
        if not isinstance(descriptor.get(field), dict):
            raise ValueError('descriptor for %s lacks a %s object schema' % (skill_id, field))


class MasterCatalog:
    """Immutable reconciliation view; mutable only via root handler injection."""

    def __init__(self, existing_descriptors=None, handlers=None, source=None):
        self._source = source if source is not None else _load_source()
        src = self._source
        self._primary_ids = list(src['az']['primary_ids'])
        self._registry_index = dict(src['registry_index'])
        self._snapshots = src['exact_match_snapshots']
        self._restricted = set(src['restricted_policy']['all_restricted_ids'])
        self._restricted_categories = {}
        for name, body in src['restricted_policy']['categories'].items():
            for skill_id in body['ids']:
                self._restricted_categories[skill_id] = name
        self._ambiguity = dict(src['domain_ambiguity'])
        self._connector_ns = src['connector_namespace']
        self._entries = {}
        self._registry_families = {}
        for skill_id in sorted(self._registry_index):
            self._registry_families.setdefault(skill_id.split('.')[0], []).append(skill_id)

        if existing_descriptors is None:
            self._descriptors = dict(self._snapshots)
            self._registry_drift = None
        else:
            if not isinstance(existing_descriptors, dict):
                raise ValueError('existing_descriptors must be a dict of id -> descriptor')
            self._descriptors = {}
            for skill_id, descriptor in existing_descriptors.items():
                _validate_descriptor(skill_id, descriptor)
                self._descriptors[skill_id] = descriptor
            self._registry_drift = {
                'descriptor_count': len(self._descriptors),
                'frozen_registry_count': len(self._registry_index),
                'exact_ids_present': sorted(
                    s for s in self._primary_ids if s in self._descriptors),
            }

        self._handlers = {}
        self._entries = {skill_id: self._build_entry(skill_id)
                         for skill_id in self._primary_ids}
        if handlers:
            if not isinstance(handlers, dict):
                raise ValueError('handlers must be a dict of id -> callable')
            self.install_handlers(handlers)

    # -- reconciliation ------------------------------------------------------
    def _status(self, skill_id):
        if skill_id in self._restricted:
            return STATUS_RESTRICTED
        if skill_id in self._snapshots:
            return STATUS_EXACT
        if skill_id.startswith(self._connector_ns):
            return STATUS_CONNECTOR
        return STATUS_MISSING

    def _related_ids(self, skill_id):
        related = self._registry_families.get(skill_id.split('.')[0], [])
        return [r for r in related if r != skill_id][:MAX_RELATED]

    def _build_entry(self, skill_id):
        az = self._source['az']
        lines = az['duplicates'].get(skill_id, {}).get('lines')
        entry = {
            'id': skill_id,
            'status': self._status(skill_id),
            'occurrences': len(lines) if lines else 1,
            'first_source_line': az['first_source_line'].get(skill_id),
            'duplicate_source_lines': lines,
            'registered_implies_operational': False,
            'operational': False,
        }
        if entry['status'] == STATUS_RESTRICTED:
            entry['policy_category'] = self._restricted_categories[skill_id]
            entry['handler_binding'] = 'forbidden'
        elif entry['status'] == STATUS_EXACT:
            descriptor = self._descriptors[skill_id]
            entry['registry_worker'] = descriptor.get('worker')
            entry['domain_alignment'] = 'cross_domain'
            entry['ambiguity_note'] = self._ambiguity.get(skill_id)
            entry['handler_binding'] = 'root_injected_only'
        elif entry['status'] == STATUS_CONNECTOR:
            entry['connector_namespace'] = self._connector_ns
            entry['handler_binding'] = 'forbidden_until_connector_configured'
        else:
            entry['handler_binding'] = 'none'
        related = self._related_ids(skill_id)
        if related and entry['status'] != STATUS_EXACT:
            entry['related_registry_ids'] = related
            entry['related_by'] = 'family_token'
            entry['related_ids_are_not_aliases'] = True
        return entry

    # -- public API ------------------------------------------------------------
    def install_handlers(self, handlers):
        """Root-only injection; rejected for restricted/missing/connector IDs."""
        for skill_id, handler in handlers.items():
            if skill_id not in self._entries:
                raise ValueError('handler rejected: %s is not a primary catalog ID' % skill_id)
            status = self._status(skill_id)
            if status == STATUS_RESTRICTED:
                raise ValueError('handler rejected: %s is restricted_catalog_only' % skill_id)
            if status != STATUS_EXACT:
                raise ValueError('handler rejected: %s has status %s' % (skill_id, status))
            if not callable(handler):
                raise ValueError('handler for %s must be callable' % skill_id)
            _validate_descriptor(skill_id, self._descriptors[skill_id])
        for skill_id, handler in handlers.items():
            self._handlers[skill_id] = handler
            self._entries[skill_id]['operational'] = True

    def lookup(self, skill_id):
        """Descriptor for exact-ID registered skills; SkillUnavailable otherwise."""
        entry = self._entries.get(skill_id)
        if entry is None:
            raise SkillUnavailable(skill_id, 'unknown_id',
                                   'not present in the frozen A-Z index')
        if entry['status'] == STATUS_RESTRICTED:
            raise SkillUnavailable(skill_id, STATUS_RESTRICTED,
                                   'policy %s; handler binding forbidden'
                                   % entry['policy_category'])
        if entry['status'] == STATUS_CONNECTOR:
            raise SkillUnavailable(skill_id, STATUS_CONNECTOR,
                                   'external connector namespace not configured')
        if entry['status'] == STATUS_MISSING:
            raise SkillUnavailable(skill_id, STATUS_MISSING,
                                   'no registered counterpart; implementation_missing')
        return {
            'descriptor': deepcopy(self._descriptors[skill_id]),
            'catalog_entry': deepcopy(entry),
            'handler_installed': skill_id in self._handlers,
            'operational': entry['operational'],
            'registered_implies_operational': False,
        }

    def entries(self):
        return deepcopy([self._entries[k] for k in self._primary_ids])

    def stats(self):
        counts = {STATUS_RESTRICTED: 0, STATUS_EXACT: 0,
                  STATUS_CONNECTOR: 0, STATUS_MISSING: 0}
        for entry in self._entries.values():
            counts[entry['status']] += 1
        detailed = sum(e['skills'] for e in self._source['engines']
                       if isinstance(e['skills'], int))
        declared = self._source['declared']['total_skills']
        phases = sum(p['skills'] for p in self._source['phases'])
        return {
            'schema_version': SCHEMA_VERSION,
            'source_sha256': self._source['source']['sha256'],
            'primary_ids': len(self._primary_ids),
            'unique_ids': self._source['az']['unique_ids'],
            'total_index_entries': self._source['az']['total_entries'],
            'duplicate_occurrences': self._source['az']['duplicate_occurrences'],
            'declared_total_skills': declared,
            'detailed_breakdown_skills': detailed,
            'declared_minus_breakdown': declared - detailed,
            'declared_engines': self._source['declared']['total_engines'],
            'detailed_engines': sum(1 for e in self._source['engines']
                                    if isinstance(e['skills'], int)),
            'narrative_only_engines': sum(1 for e in self._source['engines']
                                          if not isinstance(e['skills'], int)),
            'phase_plan_skills_sum': phases,
            'status_counts': counts,
            'exact_registered_ids': sorted(s for s in self._primary_ids
                                           if self._status(s) == STATUS_EXACT),
            'restricted_by_category': {
                name: len(body['ids'])
                for name, body in self._source['restricted_policy']['categories'].items()},
            'registry_descriptors': (self._registry_drift['descriptor_count']
                                     if self._registry_drift else
                                     len(self._registry_index)),
            'registry_drift': deepcopy(self._registry_drift),
            'operational_skills': sum(1 for e in self._entries.values()
                                      if e['operational']),
            'registered_implies_operational': False,
        }

    def snapshot(self):
        return {
            'schema_version': SCHEMA_VERSION,
            'source': deepcopy(self._source['source']),
            'declared': deepcopy(self._source['declared']),
            'counts': {k: v for k, v in self.stats().items()},
            'skills': self.entries(),
        }


_DEFAULT = MasterCatalog()


def catalog(existing_descriptors=None, handlers=None):
    """Full deterministic reconciliation snapshot (fresh copy per call)."""
    if existing_descriptors is None and not handlers:
        return _DEFAULT.snapshot()
    return MasterCatalog(existing_descriptors=existing_descriptors,
                         handlers=handlers).snapshot()


def stats():
    return _DEFAULT.stats()


def lookup(skill_id):
    return _DEFAULT.lookup(skill_id)


def configure(existing_descriptors=None, handlers=None):
    """Root integration point: replace the default instance (manual, atomic)."""
    global _DEFAULT
    _DEFAULT = MasterCatalog(existing_descriptors=existing_descriptors,
                             handlers=handlers)
    return _DEFAULT.stats()

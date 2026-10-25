"""Pure Flutter source modules. No generated source is executed by this worker."""
import copy
import re

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 64,
                 'pattern': r'^[A-Za-z][A-Za-z0-9 _-]{0,63}$'},
        'identifier': {'type': 'string', 'maxLength': 100,
                       'pattern': r'^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$'},
        'framework': {'type': 'string', 'enum': ['flutter']},
    },
}

HEADER = '// Generated source only; not compiled, installed, or executed by Harmonika.\n'

RIVERPOD = r'''import 'package:flutter_riverpod/flutter_riverpod.dart';

enum StorePhase { idle, loading, ready, failed }

class AppStoreState {
  final Map<String, String> values;
  final StorePhase phase;
  // Fixed error code: never expose raw exceptions, tokens, or response bodies.
  final String? errorCode;
  AppStoreState({Map<String, String> values = const {},
    this.phase = StorePhase.idle, this.errorCode})
      : values = Map.unmodifiable(values);
}

final appStoreProvider = NotifierProvider<AppStore, AppStoreState>(AppStore.new);

class AppStore extends Notifier<AppStoreState> {
  int _generation = 0;
  bool _disposed = false;

  @override
  AppStoreState build() {
    _disposed = false;
    ref.onDispose(() { _disposed = true; _generation++; });
    return AppStoreState();
  }

  void setValue(String key, String value) {
    if (_disposed) return;
    _generation++; // Editing invalidates a pending replacement load.
    state = AppStoreState(values: {...state.values, key: value}, phase: StorePhase.ready);
  }

  void removeValue(String key) {
    if (_disposed) return;
    _generation++;
    final next = {...state.values}..remove(key);
    state = AppStoreState(values: next, phase: StorePhase.ready);
  }

  Future<void> load(Future<Map<String, String>> Function() read) async {
    if (_disposed) return;
    final generation = ++_generation;
    state = AppStoreState(values: state.values, phase: StorePhase.loading);
    try {
      final next = await read().timeout(const Duration(seconds: 20));
      if (_disposed || generation != _generation) return;
      state = AppStoreState(values: next, phase: StorePhase.ready);
    } catch (_) {
      if (_disposed || generation != _generation) return;
      state = AppStoreState(values: state.values, phase: StorePhase.failed,
        errorCode: 'load_failed');
    }
  }

  void reset() {
    if (_disposed) return;
    _generation++;
    state = AppStoreState();
  }
}
'''

BLOC = r'''import 'package:flutter_bloc/flutter_bloc.dart';

enum ResourcePhase { idle, loading, ready, failed }

class ResourceState<T> {
  final ResourcePhase phase;
  final T? value;
  final String? errorCode;
  const ResourceState(this.phase, {this.value, this.errorCode});
}

/// Supply immutable T values; Cubit does not deep-copy caller-owned objects.
class ResourceCubit<T> extends Cubit<ResourceState<T>> {
  int _generation = 0;
  ResourceCubit() : super(ResourceState<T>(ResourcePhase.idle));

  Future<void> load(Future<T> Function() read) async {
    if (isClosed) return;
    final generation = ++_generation;
    emit(ResourceState<T>(ResourcePhase.loading, value: state.value));
    try {
      final value = await read().timeout(const Duration(seconds: 20));
      if (isClosed || generation != _generation) return;
      emit(ResourceState<T>(ResourcePhase.ready, value: value));
    } catch (_) {
      if (isClosed || generation != _generation) return;
      emit(ResourceState<T>(ResourcePhase.failed, value: state.value,
        errorCode: 'load_failed'));
    }
  }

  void reset() {
    if (isClosed) return;
    _generation++;
    emit(ResourceState<T>(ResourcePhase.idle));
  }

  @override
  Future<void> close() {
    _generation++; // A late load may finish, but cannot emit after disposal.
    return super.close();
  }
}
'''

SQLITE = r'''import 'dart:convert';
import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

class RevisionConflict implements Exception {
  const RevisionConflict();
  @override
  String toString() => 'revision_conflict';
}

class StoredRecord {
  final String id;
  final Map<String, Object?> payload;
  final int revision;
  final int updatedAt;
  StoredRecord(this.id, Map<String, Object?> payload, this.revision, this.updatedAt)
      : payload = Map.unmodifiable(payload);
}

class MobileDatabase {
  static const schemaVersion = 2;
  final Database _db;
  bool _closed = false;
  MobileDatabase._(this._db);

  // Use one owner per DB file; application startup owns opening/closing it.
  static Future<MobileDatabase> open() async {
    final path = p.join(await getDatabasesPath(), 'mobile_records.db');
    final db = await openDatabase(path, version: schemaVersion,
      onCreate: (db, version) async {
        await db.execute('CREATE TABLE records (id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0)');
      },
      // sqflite runs onUpgrade inside its opening transaction. Do not nest one.
      onUpgrade: (db, oldVersion, newVersion) async {
        if (oldVersion < 2) {
          await db.execute('ALTER TABLE records ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0');
        }
      },
      onDowngrade: (db, oldVersion, newVersion) async {
        throw StateError('database_downgrade_not_supported');
      });
    return MobileDatabase._(db);
  }

  void _checkOpen() { if (_closed) throw StateError('database_closed'); }

  StoredRecord _decode(Map<String, Object?> row) => StoredRecord(
    row['id'] as String,
    Map<String, Object?>.from(jsonDecode(row['payload'] as String) as Map),
    row['revision'] as int, row['updated_at'] as int);

  Future<StoredRecord?> read(String id) async {
    _checkOpen();
    final rows = await _db.query('records', where: 'id = ?', whereArgs: [id], limit: 1);
    return rows.isEmpty ? null : _decode(rows.single);
  }

  // expectedRevision == null means create-only; never INSERT OR REPLACE.
  Future<StoredRecord> save(String id, Map<String, Object?> payload,
      {int? expectedRevision}) async {
    _checkOpen();
    if (id.isEmpty || id.length > 128 || (expectedRevision != null && expectedRevision < 1)) {
      throw ArgumentError('invalid_record_or_revision');
    }
    final encoded = jsonEncode(payload);
    if (utf8.encode(encoded).length > 65536) throw ArgumentError('record_too_large');
    return _db.transaction((txn) async {
      final rows = await txn.query('records', where: 'id = ?', whereArgs: [id], limit: 1);
      final current = rows.isEmpty ? null : rows.single['revision'] as int;
      if (current != expectedRevision) throw const RevisionConflict();
      final next = (current ?? 0) + 1;
      final updatedAt = DateTime.now().toUtc().millisecondsSinceEpoch;
      final values = {'id': id, 'payload': encoded, 'revision': next, 'updated_at': updatedAt};
      if (current == null) {
        await txn.insert('records', values, conflictAlgorithm: ConflictAlgorithm.abort);
      } else {
        final changed = await txn.update('records', values,
          where: 'id = ? AND revision = ?', whereArgs: [id, expectedRevision]);
        if (changed != 1) throw const RevisionConflict();
      }
      return StoredRecord(id, Map<String, Object?>.from(jsonDecode(encoded) as Map), next, updatedAt);
    });
  }

  Future<void> delete(String id, {required int expectedRevision}) async {
    _checkOpen();
    await _db.transaction((txn) async {
      final count = await txn.delete('records', where: 'id = ? AND revision = ?',
        whereArgs: [id, expectedRevision]);
      if (count != 1) throw const RevisionConflict();
    });
  }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _db.close();
  }
}
'''

HIVE = r'''import 'dart:convert';
import 'package:hive_flutter/hive_flutter.dart';

/// Non-sensitive JSON data only. This box is not an encrypted credential store.
class HiveRecords {
  final Box<String> _box;
  bool _closed = false;
  HiveRecords._(this._box);

  // First call Hive.initFlutter() once at application startup. A single owner
  // opens this box and owns closing it; do not close Hive globally from a widget.
  static Future<HiveRecords> open() async => HiveRecords._(
    await Hive.openBox<String>('mobile_non_sensitive_records'));

  void _check(String key) {
    if (_closed || !_box.isOpen) throw StateError('store_closed');
    if (key.isEmpty || key.length > 128) throw ArgumentError('invalid_key');
  }

  Map<String, Object?>? read(String key) {
    _check(key);
    final raw = _box.get(key);
    return raw == null ? null : Map.unmodifiable(
      Map<String, Object?>.from(jsonDecode(raw) as Map));
  }

  Future<void> write(String key, Map<String, Object?> value) async {
    _check(key);
    final encoded = jsonEncode(value);
    if (utf8.encode(encoded).length > 65536) throw ArgumentError('record_too_large');
    await _box.put(key, encoded);
  }

  Future<void> remove(String key) async { _check(key); await _box.delete(key); }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _box.close();
  }
}
'''

MIGRATION = r'''import 'package:sqflite/sqflite.dart';

typedef MigrationStep = Future<void> Function(DatabaseExecutor database);

/// Keys are destination versions: 1 creates a new schema, 2 upgrades 1 -> 2.
/// Construct this registry in trusted source, never from a remote spec or SQL.
class MigrationPlan {
  final int latest;
  final Map<int, MigrationStep> _steps;
  MigrationPlan(this.latest, Map<int, MigrationStep> steps)
      : _steps = Map.unmodifiable(steps) {
    if (latest < 1 || latest > 100 || _steps.length != latest ||
        List.generate(latest, (i) => i + 1).any((n) => !_steps.containsKey(n))) {
      throw ArgumentError('migration_steps_must_be_contiguous');
    }
  }

  Future<void> _apply(DatabaseExecutor db, int oldVersion, int newVersion) async {
    if (oldVersion < 0 || newVersion != latest || oldVersion > newVersion) {
      throw StateError('unsupported_schema_version');
    }
    for (var version = oldVersion + 1; version <= newVersion; version++) {
      await _steps[version]!(db);
    }
  }

  /// Opening callbacks execute atomically in sqflite's built-in transaction.
  /// A thrown migration rolls back; never swallow an exception or recreate data.
  Future<Database> open(String path) => openDatabase(path, version: latest,
    onCreate: (db, version) => _apply(db, 0, version),
    onUpgrade: (db, oldVersion, newVersion) => _apply(db, oldVersion, newVersion),
    onDowngrade: (db, oldVersion, newVersion) async {
      throw StateError('schema_downgrade_refused');
    });
}

final exampleRecordMigrations = MigrationPlan(2, {
  1: (db) => db.execute('CREATE TABLE migration_records (id TEXT PRIMARY KEY, payload TEXT NOT NULL)'),
  2: (db) => db.execute('ALTER TABLE migration_records ADD COLUMN revision INTEGER NOT NULL DEFAULT 1'),
});
'''

CONFLICT = r'''import 'dart:convert';

class MergeResult {
  // Null on any conflict: a caller cannot mistake partial output for a safe write.
  final Map<String, Object?>? merged;
  final List<String> conflicts;
  final bool baseKnown;
  MergeResult(Map<String, Object?>? merged, List<String> conflicts, this.baseKnown)
      : merged = merged == null ? null : Map.unmodifiable(merged),
        conflicts = List.unmodifiable(conflicts);
  bool get resolved => baseKnown && conflicts.isEmpty && merged != null;
}

bool _same(Object? a, Object? b) {
  if (a is Map && b is Map) {
    return a.length == b.length && a.keys.every((k) => b.containsKey(k) && _same(a[k], b[k]));
  }
  if (a is List && b is List) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) { if (!_same(a[i], b[i])) return false; }
    return true;
  }
  return a == b;
}

Map<String, Object?> _snapshot(Map<String, Object?> value) {
  // Validate/copy JSON input so later caller mutation cannot change a decision.
  final encoded = jsonEncode(value);
  if (utf8.encode(encoded).length > 65536) throw ArgumentError('merge_document_too_large');
  return Map<String, Object?>.from(jsonDecode(encoded) as Map);
}

/// Conservative three-way merge of JSON documents, per top-level field.
/// Nested-object concurrent changes conflict as a whole; no guessed recursion.
/// Missing key means deletion, which is distinct from a present null value.
MergeResult mergeRecords({required Map<String, Object?>? base,
    required Map<String, Object?> local, required Map<String, Object?> remote}) {
  if (base == null) return MergeResult(null, const ['unknown_base'], false);
  final b = _snapshot(base), l = _snapshot(local), r = _snapshot(remote);
  final keys = {...b.keys, ...l.keys, ...r.keys}.toList()..sort();
  final merged = <String, Object?>{};
  final conflicts = <String>[];
  for (final key in keys) {
    bool equal(Map<String, Object?> x, Map<String, Object?> y) =>
      x.containsKey(key) == y.containsKey(key) && _same(x[key], y[key]);
    Map<String, Object?>? chosen;
    if (equal(l, r)) { chosen = l; }
    else if (equal(l, b)) { chosen = r; }
    else if (equal(r, b)) { chosen = l; }
    else { conflicts.add(key); }
    if (chosen != null && chosen.containsKey(key)) merged[key] = chosen[key];
  }
  return MergeResult(conflicts.isEmpty ? merged : null, conflicts, true);
}
'''

SYNC = r'''import 'dart:convert';
import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

enum PushStatus { accepted, conflict, retry }

class PendingMutation {
  final String requestId, recordId;
  final int baseRevision;
  final Map<String, Object?> payload;
  PendingMutation(this.requestId, this.recordId, this.baseRevision,
      Map<String, Object?> payload) : payload = Map.unmodifiable(payload);
}

class PushReply {
  final String requestId;
  final PushStatus status;
  final int? revision;
  const PushReply(this.requestId, this.status, {this.revision});
}

typedef PushMutation = Future<PushReply> Function(PendingMutation mutation);

/// Durable push outbox. Remote CAS + idempotency are required server contracts.
/// No network or background scheduling is hidden in this class.
class SyncOutbox {
  final Database _db;
  Future<int>? _draining;
  bool _closed = false;
  SyncOutbox._(this._db);

  static Future<SyncOutbox> open() async {
    final db = await openDatabase(p.join(await getDatabasesPath(), 'mobile_outbox.db'),
      version: 1, onCreate: (db, version) async {
        await db.execute('CREATE TABLE outbox (sequence INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, record_id TEXT NOT NULL UNIQUE, base_revision INTEGER NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT \'pending\')');
      }, onDowngrade: (db, oldVersion, newVersion) async {
        throw StateError('outbox_downgrade_refused');
      });
    return SyncOutbox._(db);
  }

  Future<void> enqueue(PendingMutation mutation) async {
    if (_closed) throw StateError('outbox_closed');
    if (!RegExp(r'^[A-Za-z0-9_-]{1,128}$').hasMatch(mutation.requestId) ||
        mutation.recordId.isEmpty || mutation.recordId.length > 128 || mutation.baseRevision < 0) {
      throw ArgumentError('invalid_mutation');
    }
    final payload = jsonEncode(mutation.payload);
    if (utf8.encode(payload).length > 65536) throw ArgumentError('mutation_too_large');
    await _db.transaction((txn) async {
      final existing = await txn.query('outbox', where: 'request_id = ?',
        whereArgs: [mutation.requestId], limit: 1);
      if (existing.isNotEmpty) {
        final row = existing.single;
        if (row['record_id'] == mutation.recordId && row['base_revision'] == mutation.baseRevision &&
            row['payload'] == payload) return;
        throw StateError('idempotency_key_reused_with_different_input');
      }
      // Unique record_id rejects a second pending edit until the first resolves.
      await txn.insert('outbox', {'request_id': mutation.requestId,
        'record_id': mutation.recordId, 'base_revision': mutation.baseRevision,
        'payload': payload, 'status': 'pending'}, conflictAlgorithm: ConflictAlgorithm.abort);
    });
  }

  Future<List<PendingMutation>> conflicts() async {
    if (_closed) throw StateError('outbox_closed');
    final rows = await _db.query('outbox', where: 'status = ?', whereArgs: ['conflict'],
      orderBy: 'sequence ASC', limit: 100);
    return rows.map(_decode).toList();
  }

  PendingMutation _decode(Map<String, Object?> row) => PendingMutation(
    row['request_id'] as String, row['record_id'] as String, row['base_revision'] as int,
    Map<String, Object?>.from(jsonDecode(row['payload'] as String) as Map));

  Future<int> drain(PushMutation push) {
    if (_closed) throw StateError('outbox_closed');
    return _draining ??= _run(push).whenComplete(() { _draining = null; });
  }

  Future<int> _run(PushMutation push) async {
    var accepted = 0;
    for (var count = 0; count < 50 && !_closed; count++) {
      final rows = await _db.query('outbox', where: 'status = ?', whereArgs: ['pending'],
        orderBy: 'sequence ASC', limit: 1);
      if (rows.isEmpty) break;
      final row = rows.single;
      final mutation = _decode(row);
      PushReply reply;
      try { reply = await push(mutation).timeout(const Duration(seconds: 20)); }
      catch (_) { break; } // Keep persisted input for explicit later retry.
      if (reply.requestId != mutation.requestId) throw StateError('invalid_acknowledgment');
      if (reply.status == PushStatus.retry) break;
      if (reply.status == PushStatus.conflict) {
        await _db.update('outbox', {'status': 'conflict'},
          where: 'request_id = ?', whereArgs: [mutation.requestId]);
        continue; // Conflict remains persisted and never overwrites remote data.
      }
      if (reply.revision == null || reply.revision! <= mutation.baseRevision) {
        throw StateError('invalid_remote_revision');
      }
      await _db.transaction((txn) async {
        await txn.delete('outbox', where: 'request_id = ? AND payload = ?',
          whereArgs: [mutation.requestId, row['payload']]);
      });
      accepted++;
    }
    return accepted;
  }

  // Only after presenting the conflict and obtaining an explicit caller decision.
  // Discard never writes remote data. Re-enqueue the merged edit with a fresh
  // requestId and the observed current server revision.
  Future<void> discardConflict(String requestId) async {
    if (_closed) throw StateError('outbox_closed');
    final count = await _db.delete('outbox', where: 'request_id = ? AND status = ?',
      whereArgs: [requestId, 'conflict']);
    if (count != 1) throw StateError('conflict_not_found');
  }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    try { await _draining; } finally { await _db.close(); }
  }
}
'''

MODULES = {
    'state.setup.riverpod': ('lib/state/app_store.dart', RIVERPOD,
        'flutter_riverpod',
        'Wrap the application in ProviderScope. Read/watch appStoreProvider from '
        'ConsumerWidget; call ref.read(appStoreProvider.notifier).load(read) or setValue. '
        'The loader is caller supplied, not an invented API. reset() on account logout. '
        'Provider disposal ignores late results; timeout does not cancel caller IO.'),
    'state.setup.bloc': ('lib/state/resource_cubit.dart', BLOC,
        'flutter_bloc',
        'Use BlocProvider(create: (_) => ResourceCubit<YourImmutableModel>()). '
        'Render idle/loading/ready/failed through BlocBuilder. BlocProvider owns close; '
        'a manually created Cubit must be closed by its owner. Call reset on logout. '
        'Timeout ignores late results but cannot cancel caller IO.'),
    'local.sqlite': ('lib/storage/mobile_database.dart', SQLITE,
        'sqflite path',
        'After WidgetsFlutterBinding.ensureInitialized(), open MobileDatabase once '
        'and inject it into repositories. save(id, payload) is create-only; updates '
        'require the revision returned by read/save. Handle RevisionConflict by '
        'rereading and asking the caller to resolve; do not retry with an assumed '
        'revision. v1 is records(id,payload,revision); v2 adds updated_at. '
        'Opening upgrades are transactional and downgrades are refused. '
        'Own and await close(). Database is not encrypted. Add a tenant/account '
        'namespace to record IDs or separate databases, and clear account data '
        'under your logout policy. Do not store credential material here.'),
    'local.hive': ('lib/storage/hive_records.dart', HIVE,
        'hive_flutter',
        'Call WidgetsFlutterBinding.ensureInitialized() then await Hive.initFlutter() '
        'once in main before opening HiveRecords. Inject one box owner; await close() '
        'when shutting that owner down. Account-scoped keys are required if sharing '
        'a box. Only bounded non-sensitive JSON maps are stored; concurrent '
        'read/modify/write and cross-record transactions are not provided. '
        'Use SQLite if atomic multi-record updates are required.'),
    'local.migration': ('lib/storage/migration_plan.dart', MIGRATION,
        'sqflite',
        'Construct an immutable contiguous MigrationPlan in trusted application '
        'source and call plan.open(databasePath). The returned Database belongs to '
        'the caller: await its close(). The included example migrates a dedicated '
        'migration_records table, not MobileDatabase.records. Test upgrades against '
        'a backup fixture before changing schema. sqflite owns callback transactions; '
        'do not start nested transactions or swallow errors. No destructive fallback '
        'or downgrade is attempted.'),
    'local.conflict.resolve': ('lib/sync/three_way_merge.dart', CONFLICT,
        '',
        'Pass the last common server snapshot as base plus local and newly fetched '
        'remote maps. Only resolved == true permits a candidate write. Unknown '
        'base or any concurrently edited field returns merged == null. Missing '
        'keys represent deletion; explicit null remains a value. Nested documents '
        'are compared structurally, but simultaneous nested edits conflict '
        'conservatively. Persist only using the remote revision in a server '
        'compare-and-swap; a merge result is not server authorization.'),
    'local.sync.engine': ('lib/sync/sync_outbox.dart', SYNC,
        'sqflite path',
        'Open one SyncOutbox owner per database and inject a PushMutation callback. '
        'The server MUST atomically check baseRevision, authorize record ownership, '
        'and persist/replay the same result for requestId across retries. Use a '
        'new globally unique requestId per user mutation. Reusing a completed key '
        'with different input must also be rejected server-side. This module is a '
        'durable push outbox, not a remote subscription or complete replication '
        'protocol; it never claims to update a local canonical cache. drain() is '
        'single-flight and bounded to 50 attempts. Schedule drains explicitly '
        'with connectivity/backoff in the app; no hidden background work. '
        'A timeout does not cancel IO: late remote effects make server idempotency '
        'mandatory. conflicts() exposes retained conflicts; after an explicit '
        'resolution, discardConflict and enqueue a new mutation against the '
        'observed server revision. close() prevents further drains and awaits '
        'the current one before closing the database. Use one account per outbox '
        'and separate/clear the DB on account changes. No credential storage.'),
}


def _validate(value):
    if type(value) is not dict or set(value) - set(SCHEMA['properties']):
        raise ValueError('invalid_input_fields')
    if 'framework' in value and value['framework'] != 'flutter':
        raise ValueError('unsupported_framework')
    if 'name' in value and (type(value['name']) is not str or not re.fullmatch(
            r'[A-Za-z][A-Za-z0-9 _-]{0,63}', value['name'])):
        raise ValueError('invalid_name')
    if 'identifier' in value and (type(value['identifier']) is not str
            or len(value['identifier']) > 100 or not re.fullmatch(
                r'[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+', value['identifier'])):
        raise ValueError('invalid_identifier')


def descriptors():
    return {skill_id: {
        'description': 'Generate a Flutter source module: ' + skill_id + '; no runtime execution.',
        'input_schema': copy.deepcopy(SCHEMA), 'mode': 'source_generation',
    } for skill_id in MODULES}


def dispatch(skill_id, value):
    if type(skill_id) is not str or skill_id not in MODULES:
        raise ValueError('unknown_skill')
    _validate(value)
    path, source, packages, instructions = MODULES[skill_id]
    install = ('Run `flutter pub add ' + packages + '` locally to resolve SDK-compatible '
               'versions; commit the reviewed pubspec.lock.\n\n') if packages else (
               'Uses Dart core libraries only; no third-party dependency.\n\n')
    readme = ('# Flutter module: ' + skill_id + '\n\n'
              'Source generation only. No Flutter SDK, emulator, build, dependency '
              'installation, database migration, network request, or device test '
              'was executed by Harmonika. No credentials or store accounts are '
              'configured. This is a repository module, not a complete app.\n\n'
              + install + instructions + '\n\n'
              'Integrate the file at `' + path + '`. Dependency/API compatibility '
              'must be checked using `flutter analyze` and meaningful device/unit '
              'tests in the target project. No runtime result is asserted.\n')
    return {
        'status': 'completed', 'mode': 'source_generation',
        'files': [{'path': path, 'content': HEADER + source},
                  {'path': 'docs/modules/' + skill_id + '.md', 'content': readme}],
        'warnings': ['Flutter source only; not compiled or executed. Review dependencies and integration.'],
        'runtime_verified': False, 'external_actions_performed': False,
    }

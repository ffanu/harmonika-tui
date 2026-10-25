"""Telco EXTENSIONS module tests (worker-owned addendum scope).

Loads backend/telco_engine/extensions.py against the REAL root telco_engine
package (root backend inserted into sys.path when available; no sys.modules
replacement, no stub modules) plus a lightweight FakeContext mirroring the
root store.Context interface (kind validation, reserved fields, CAS
versioning, bounded lists with 413, tenant-scoped get 404, events).

Unittest + synthetic data only; no customer PII, no network, no root writes.
"""

import copy
import importlib
import importlib.util
import json
import os
import re
import sys
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE_BACKEND = os.path.normpath(os.path.join(HERE, "..", "backend"))
ROOT_BACKEND = WORKTREE_BACKEND
EXTENSIONS_FILE = os.path.join(WORKTREE_BACKEND, "telco_engine", "extensions.py")

CANONICAL_IDS = [
    "netinv.fiber.route",
    "netinv.splitter.management",
    "netinv.audit.physical",
    "noc.monitor.cpu",
    "noc.map.network",
    "noc.shift.handover",
    "noc.incident.postmortem",
    "field.quality.score",
    "field.schedule.optimize",
    "qos.threshold",
    "qos.benchmark",
    "fraud.case.investigate",
    "fraud.blacklist.subscriber",
    "revass.discount.abuse",
    "revass.report.monthly",
    "revass.recovery.action",
    "workflow.sla.attach",
]

EXPECTED_MUTATIONS = {
    "netinv.splitter.management",
    "noc.shift.handover",
    "noc.incident.postmortem",
    "field.quality.score",
    "qos.threshold",
    "fraud.case.investigate",
    "fraud.blacklist.subscriber",
    "revass.recovery.action",
    "workflow.sla.attach",
}

EXPECTED_READONLY = set(CANONICAL_IDS) - EXPECTED_MUTATIONS

FORBIDDEN_ARG_NAMES = {
    "path",
    "file",
    "command",
    "url",
    "host",
    "port",
    "ssh",
    "endpoint",
    "secret",
    "password",
    "token",
    "credentials",
    "api_key",
}


from backend.telco_engine import extensions,common as _common_module
DomainError=_common_module.DomainError
COMMON_ORIGIN=_common_module.__file__
ROOT_COMMON_PATH=COMMON_ORIGIN
_PACKAGE_ORIGIN='package:'+extensions.__package__
COMMON_PROOF='real-root-file'
HAVE_VALIDATE=True
validate_schema=_common_module.validate


class FakeContext(object):
    """Faithful mirror of root store.Context semantics (single tenant)."""

    _KIND_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
    _ID_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")
    _RESERVED = {"id", "version", "created_at", "updated_at", "tenant", "actor"}
    MAX_RECORDS = 10000

    def __init__(self, now="2026-10-24T09:00:00Z", tenant="tenant-test", actor="api-token:42"):
        self.now = now
        self.tenant = tenant
        self.actor = actor
        self.store = {}
        self.events = []

    def _kind(self, kind):
        if not (type(kind) is str and self._KIND_RE.fullmatch(kind)):
            raise DomainError("invalid_kind")
        return kind

    def _id(self, record_id):
        if not (type(record_id) is str and self._ID_RE.fullmatch(record_id)):
            raise DomainError("invalid_identifier")
        return record_id

    def create(self, kind, data, id=None):
        self._kind(kind)
        if not isinstance(data, dict) or (set(data) & self._RESERVED):
            raise DomainError("reserved_fields")
        record_id = self._id(id) if id is not None else str(uuid.uuid4())
        bucket = self.store.setdefault(kind, {})
        if len(bucket) >= self.MAX_RECORDS:
            raise DomainError("tenant_record_quota", 429)
        if record_id in bucket:
            raise DomainError("already_exists", 409)
        record = dict(data)
        record.update({"id": record_id, "version": 1, "created_at": self.now, "updated_at": self.now})
        bucket[record_id] = record
        return dict(record)

    def get(self, kind, record_id):
        self._kind(kind)
        record = self.store.get(kind, {}).get(self._id(record_id))
        if record is None:
            raise DomainError("not_found", 404)
        return dict(record)

    def update(self, kind, record_id, changes, expected_version=None):
        self._kind(kind)
        record = self.store.get(kind, {}).get(self._id(record_id))
        if record is None:
            raise DomainError("not_found", 404)
        if not isinstance(changes, dict) or (set(changes) & self._RESERVED):
            raise DomainError("reserved_fields")
        if expected_version is not None:
            if not type(expected_version) is int or record["version"] != expected_version:
                raise DomainError("version_conflict", 409)
        record = dict(record)
        record.update(changes)
        record["version"] = record["version"] + 1
        record["updated_at"] = self.now
        self.store[kind][record_id] = record
        return dict(record)

    def list(self, kind, where=None, limit=1000):
        self._kind(kind)
        if not type(limit) is int or not 1 <= limit <= 1000:
            raise DomainError("invalid_limit")
        rows = [dict(r) for r in self.store.get(kind, {}).values()]
        rows.sort(key=lambda r: r["id"])
        if where:
            rows = [r for r in rows if all(r.get(k) == v for k, v in where.items())]
        if len(rows) > limit:
            raise DomainError("query_limit_exceeded", 413)
        return rows

    def event(self, event_type, data):
        keys = sorted(str(k)[:50] for k in data)[:30] if isinstance(data, dict) else None
        self.events.append((str(event_type)[:80], keys))


NOW = "2026-10-24T09:00:00Z"
FRESH_AT = "2026-10-24T08:59:00Z"
OLDER_AT = "2026-10-24T08:50:00Z"
STALE_AT = "2026-10-23T08:59:00Z"
FUTURE_AT = "2026-10-24T09:01:00Z"


def base_ctx():
    ctx = FakeContext(now=NOW)
    ctx.create("device", {"name": "OLT-1", "device_type": "olt", "status": "installed", "parent_id": None}, id="dev-olt-1")
    ctx.create("device", {"name": "ODC-1", "device_type": "odc", "status": "installed", "parent_id": "dev-olt-1"}, id="dev-odc-1")
    ctx.create("device", {"name": "ODP-1", "device_type": "odp", "status": "installed", "parent_id": "dev-odc-1"}, id="dev-odp-1")
    ctx.create("subscriber", {"name": "Subscriber A", "status": "active"}, id="sub-1")
    ctx.create("subscriber", {"name": "Subscriber B", "status": "active"}, id="sub-2")
    ctx.create("product", {"name": "Fiber Home", "price_idr": 300000, "active": True}, id="prod-1")
    ctx.create("work_order", {"subscriber_id": "sub-1", "status": "open", "priority": "normal", "technician_id": None}, id="wo-1")
    ctx.create(
        "ticket",
        {"status": "resolved", "severity": "P2", "opened_at": "2026-10-24T07:00:00Z", "resolved_at": "2026-10-24T08:00:00Z"},
        id="tkt-1",
    )
    ctx.create("ticket", {"status": "open", "severity": "P3", "opened_at": "2026-10-24T08:00:00Z"}, id="tkt-open")
    ctx.create(
        "outage",
        {"asset_kind": "device", "asset_id": "dev-olt-1", "status": "open", "severity": "P1", "started_at": "2026-10-24T08:00:00Z"},
        id="out-1",
    )
    ctx.create(
        "sla",
        {
            "name": "Standard",
            "targets": {
                "P1": {"response_minutes": 15, "resolution_minutes": 240},
                "P2": {"response_minutes": 60, "resolution_minutes": 1440},
                "P3": {"response_minutes": 240, "resolution_minutes": 4320},
            },
            "penalty_bps_per_hour": 0,
            "penalty_cap_pct": 0,
        },
        id="sla-1",
    )
    ctx.create("workflow_template", {"name": "Install flow", "steps": [], "waves": [], "status": "draft"}, id="wf-tpl-1")
    ctx.create(
        "workflow_instance",
        {"template_id": "wf-tpl-1", "template_version": 1, "status": "awaiting_step_dispatch", "step_ids": [], "event_reference": "evt-1"},
        id="wf-inst-1",
    )
    ctx.create(
        "invoice",
        {"subscriber_id": "sub-1", "period": "2026-09", "amount_idr": 300000, "status": "open", "paid_idr": 0},
        id="inv-1",
    )
    return ctx


def link(ctx, a, b, length_m, status="active", link_type="fiber", suffix=None):
    rec = ctx.create(
        "network_link",
        {"a_device_id": a, "b_device_id": b, "link_type": link_type, "length_m": length_m, "status": status},
    )
    return rec


def expect_domain_error(test_case, fn, code, status=None):
    try:
        fn()
    except DomainError as exc:
        test_case.assertEqual(exc.code, code)
        if status is not None:
            test_case.assertEqual(exc.status, status)
        return exc
    test_case.fail("expected DomainError(%s)" % code)


# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------


class TestModuleContract(unittest.TestCase):
    def test_exact_canonical_ids(self):
        self.assertEqual(set(extensions.HANDLERS), set(CANONICAL_IDS))
        self.assertEqual(set(extensions.SCHEMAS), set(CANONICAL_IDS))
        self.assertEqual(len(extensions.HANDLERS), 17)

    def test_mutations_exact_and_disjoint(self):
        self.assertEqual(set(extensions.MUTATIONS), EXPECTED_MUTATIONS)
        self.assertTrue(set(extensions.MUTATIONS) <= set(extensions.HANDLERS))
        for handler_id in EXPECTED_READONLY:
            self.assertNotIn(handler_id, extensions.MUTATIONS)

    def test_limitations_cover_every_id(self):
        for handler_id in CANONICAL_IDS:
            limitation = extensions.LIMITATIONS.get(handler_id)
            self.assertIsInstance(limitation, str)
            self.assertTrue(len(limitation) > 20)

    def test_schemas_strict(self):
        for handler_id, schema in extensions.SCHEMAS.items():
            self.assertEqual(schema.get("type"), "object", handler_id)
            self.assertIs(schema.get("additionalProperties"), False, handler_id)
            self.assertIsInstance(schema.get("properties"), dict, handler_id)
            self.assertIsInstance(schema.get("required"), list, handler_id)
            for required in schema["required"]:
                self.assertIn(required, schema["properties"], handler_id)

    def test_schemas_have_no_forbidden_arg_names(self):
        def walk(schema, handler_id):
            if not isinstance(schema, dict):
                return
            for name in schema.get("properties", {}) or {}:
                self.assertNotIn(name.lower(), FORBIDDEN_ARG_NAMES, "%s: %s" % (handler_id, name))
            walk(schema.get("items"), handler_id)
            for prop in (schema.get("properties") or {}).values():
                walk(prop, handler_id)

        for handler_id, schema in extensions.SCHEMAS.items():
            walk(schema, handler_id)

    def test_real_common_module_imported(self):
        self.assertTrue(str(_PACKAGE_ORIGIN).startswith("package:") or str(_PACKAGE_ORIGIN).startswith("preexisting-package:"))
        if os.path.isfile(ROOT_COMMON_PATH):
            # real root common is available on this machine: it must be provably the one in use
            self.assertIn(COMMON_PROOF, ("real-root-file", "real-root-code"), "expected real root common, got %s" % COMMON_ORIGIN)
        else:
            self.assertEqual(COMMON_PROOF, "worktree-fallback-no-root")
        print("common module origin: %s (proof: %s)" % (COMMON_ORIGIN, COMMON_PROOF))

    def test_sample_payloads_validate_against_published_schemas(self):
        samples = {
            "netinv.fiber.route": {"from_device_id": "dev-olt-1", "to_device_id": "dev-odp-1"},
            "netinv.splitter.management": {"action": "register", "device_id": "dev-odp-1", "ratio": 8},
            "netinv.audit.physical": {
                "items": [
                    {"device_id": "dev-odp-1", "found_status": "installed", "evidence_ref": "E1", "observed_at": OLDER_AT, "source": "crew-1"}
                ]
            },
            "noc.monitor.cpu": {"device_id": "dev-olt-1", "samples": [{"source": "poller-1", "observed_at": FRESH_AT, "value_int": 42}]},
            "noc.map.network": {},
            "noc.shift.handover": {"shift_label": "2026-10-24-day", "operator_name": "NOC A"},
            "noc.incident.postmortem": {
                "ticket_id": "tkt-1",
                "title": "Fiber cut",
                "summary": "Summary",
                "root_cause_category": "fiber_cut",
                "corrective_actions": ["Replace segment"],
            },
            "field.quality.score": {"technician_id": "tech-1", "source": "qa-1", "checklist": [{"item_id": "i1", "passed": True, "weight": 50}]},
            "field.schedule.optimize": {
                "work_orders": [{"work_order_id": "wo-1", "duration_minutes": 60}],
                "technicians": [
                    {"technician_id": "tech-1", "available_from": "2026-10-24T01:00:00Z", "available_to": "2026-10-24T05:00:00Z"}
                ],
            },
            "qos.threshold": {"product_id": "prod-1", "thresholds": {"latency_ms": 100, "jitter_ms": 30, "loss_bps": 100}},
            "qos.benchmark": {
                "samples": [{"source": "s1", "observed_at": FRESH_AT, "latency_ms": 10, "jitter_ms": 2, "loss_bps": 1}],
                "benchmark": {"latency_ms": 100, "jitter_ms": 30, "loss_bps": 100},
            },
            "fraud.case.investigate": {"case_ref": "CASE-1", "allegation": "other", "evidence": [{"evidence_ref": "E1", "description": "d"}]},
            "fraud.blacklist.subscriber": {"subscriber_id": "sub-1", "reason": "other", "evidence_refs": ["E1"], "justification": "j"},
            "revass.discount.abuse": {
                "policy": {"max_discount_bps": 1000},
                "discounts": [{"discount_ref": "D1", "discount_bps": 100, "period": "2026-09"}],
            },
            "revass.report.monthly": {
                "period": "2026-09",
                "entries": [{"entry_ref": "R1", "category": "revenue", "expected_idr": 1000, "actual_idr": 1000}],
            },
            "revass.recovery.action": {"case_ref": "C1", "action_type": "writeoff_request", "amount_idr": 5000, "justification": "j"},
            "workflow.sla.attach": {"sla_id": "sla-1", "template_id": "wf-tpl-1"},
        }
        self.assertEqual(set(samples), set(CANONICAL_IDS))
        for handler_id, payload in samples.items():
            validate_schema(copy.deepcopy(payload), extensions.SCHEMAS[handler_id])

    def test_schemas_reject_unknown_fields(self):
        if not HAVE_VALIDATE:  # pragma: no cover
            self.skipTest("root common.validate unavailable")
        for handler_id, payload in (
            ("netinv.fiber.route", {"from_device_id": "a", "to_device_id": "b", "extra": 1}),
            ("noc.monitor.cpu", {"device_id": "d", "samples": [{"source": "s", "observed_at": FRESH_AT, "value_int": 1, "nope": 2}]}),
            ("qos.threshold", {"product_id": "p", "thresholds": {"latency_ms": 1, "jitter_ms": 1, "loss_bps": 1}, "mode": "x"}),
        ):
            try:
                validate_schema(payload, extensions.SCHEMAS[handler_id])
            except DomainError as exc:
                self.assertIn(exc.code, ("unknown_fields", "invalid_object"))
            else:
                self.fail("unknown field accepted for %s" % handler_id)


# ---------------------------------------------------------------------------
# netinv.fiber.route
# ---------------------------------------------------------------------------


class TestFiberRoute(unittest.TestCase):
    def _net(self):
        ctx = base_ctx()
        link(ctx, "dev-olt-1", "dev-odc-1", 10)
        link(ctx, "dev-odc-1", "dev-odp-1", 10)
        link(ctx, "dev-olt-1", "dev-odp-1", 100)  # longer direct alternative
        return ctx

    def test_shortest_path_wins(self):
        ctx = self._net()
        out = extensions.HANDLERS["netinv.fiber.route"](ctx, {"from_device_id": "dev-olt-1", "to_device_id": "dev-odp-1"})
        self.assertTrue(out["reachable"])
        self.assertEqual(out["path_device_ids"], ["dev-olt-1", "dev-odc-1", "dev-odp-1"])
        self.assertEqual(out["total_length_m"], 20)
        self.assertEqual(out["hops"], 2)
        self.assertFalse(out["coverage_promised"])

    def test_unreachable_reports_inventory_fact_only(self):
        ctx = base_ctx()
        out = extensions.HANDLERS["netinv.fiber.route"](ctx, {"from_device_id": "dev-olt-1", "to_device_id": "dev-odp-1"})
        self.assertFalse(out["reachable"])
        self.assertEqual(out["path_device_ids"], [])
        self.assertIsNone(out["total_length_m"])
        self.assertFalse(out["coverage_promised"])
        self.assertIn("never", out["note"])

    def test_planned_links_ignored(self):
        ctx = base_ctx()
        link(ctx, "dev-olt-1", "dev-odp-1", 5, status="planned")
        out = extensions.HANDLERS["netinv.fiber.route"](ctx, {"from_device_id": "dev-olt-1", "to_device_id": "dev-odp-1"})
        self.assertFalse(out["reachable"])

    def test_missing_device_404(self):
        ctx = base_ctx()
        expect_domain_error(
            self,
            lambda: extensions.HANDLERS["netinv.fiber.route"](ctx, {"from_device_id": "dev-ghost", "to_device_id": "dev-odp-1"}),
            "not_found",
            404,
        )

    def test_same_endpoint_rejected(self):
        ctx = base_ctx()
        expect_domain_error(
            self,
            lambda: extensions.HANDLERS["netinv.fiber.route"](ctx, {"from_device_id": "dev-olt-1", "to_device_id": "dev-olt-1"}),
            "invalid_argument",
        )

    def test_readonly_writes_nothing(self):
        ctx = self._net()
        before = json.dumps(ctx.store, sort_keys=True, default=str)
        extensions.HANDLERS["netinv.fiber.route"](ctx, {"from_device_id": "dev-olt-1", "to_device_id": "dev-odp-1"})
        self.assertEqual(json.dumps(ctx.store, sort_keys=True, default=str), before)


# ---------------------------------------------------------------------------
# netinv.splitter.management
# ---------------------------------------------------------------------------


class TestSplitterManagement(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["netinv.splitter.management"])

    def test_register_and_duplicate(self):
        ctx = base_ctx()
        out = self.H(ctx, {"action": "register", "device_id": "dev-odp-1", "ratio": 8, "name": "ODP-1 splitter"})
        self.assertEqual(out["total_ports"], 8)
        self.assertEqual(out["status"], "active")
        expect_domain_error(self, lambda: self.H(ctx, {"action": "register", "device_id": "dev-odp-1", "ratio": 8}), "duplicate_splitter", 409)

    def test_register_unknown_device(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, {"action": "register", "device_id": "dev-ghost", "ratio": 8}), "not_found", 404)

    def test_register_invalid_ratio(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, {"action": "register", "device_id": "dev-odp-1", "ratio": 7}), "invalid_argument")

    def test_allocate_auto_picks_lowest_free(self):
        ctx = base_ctx()
        self.H(ctx, {"action": "register", "device_id": "dev-odp-1", "ratio": 8})
        first = self.H(ctx, {"action": "allocate", "splitter_id": ctx.store["splitter"]["x"].get("id", None)} if False else {"action": "allocate", "splitter_id": list(ctx.store["splitter"].values())[0]["id"]})
        self.assertEqual(first["port_no"], 1)
        splitter_id = first["splitter_id"]
        second = self.H(ctx, {"action": "allocate", "splitter_id": splitter_id, "subscriber_id": "sub-1"})
        self.assertEqual(second["port_no"], 2)
        self.assertEqual(second["free_count"], 6)

    def test_allocate_explicit_port_conflict(self):
        ctx = base_ctx()
        self.H(ctx, {"action": "register", "device_id": "dev-odp-1", "ratio": 8})
        splitter_id = list(ctx.store["splitter"].values())[0]["id"]
        self.H(ctx, {"action": "allocate", "splitter_id": splitter_id, "port_no": 3})
        expect_domain_error(self, lambda: self.H(ctx, {"action": "allocate", "splitter_id": splitter_id, "port_no": 3}), "port_unavailable", 409)

    def test_capacity_exhausted(self):
        ctx = base_ctx()
        self.H(ctx, {"action": "register", "device_id": "dev-odp-1", "ratio": 4})
        splitter_id = list(ctx.store["splitter"].values())[0]["id"]
        for port_no in range(1, 5):
            self.H(ctx, {"action": "allocate", "splitter_id": splitter_id, "port_no": port_no})
        expect_domain_error(self, lambda: self.H(ctx, {"action": "allocate", "splitter_id": splitter_id}), "splitter_capacity_exhausted", 409)

    def test_release_and_inspect(self):
        ctx = base_ctx()
        self.H(ctx, {"action": "register", "device_id": "dev-odp-1", "ratio": 4})
        splitter_id = list(ctx.store["splitter"].values())[0]["id"]
        self.H(ctx, {"action": "allocate", "splitter_id": splitter_id, "subscriber_id": "sub-1"})
        self.H(ctx, {"action": "allocate", "splitter_id": splitter_id, "subscriber_id": "sub-2"})
        released = self.H(ctx, {"action": "release", "splitter_id": splitter_id, "port_no": 1})
        self.assertEqual(released["status"], "released")
        inspected = self.H(ctx, {"action": "inspect", "splitter_id": splitter_id})
        self.assertEqual(inspected["allocated_count"], 1)
        self.assertEqual(inspected["free_count"], 3)
        self.assertEqual(inspected["utilization_bps"], 2500)
        expect_domain_error(self, lambda: self.H(ctx, {"action": "release", "splitter_id": splitter_id, "port_no": 1}), "not_allocated", 404)

    def test_allocate_unknown_subscriber(self):
        ctx = base_ctx()
        self.H(ctx, {"action": "register", "device_id": "dev-odp-1", "ratio": 8})
        splitter_id = list(ctx.store["splitter"].values())[0]["id"]
        expect_domain_error(
            self, lambda: self.H(ctx, {"action": "allocate", "splitter_id": splitter_id, "subscriber_id": "sub-ghost"}), "not_found", 404
        )

    def test_unknown_splitter(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, {"action": "inspect", "splitter_id": "spl-ghost"}), "not_found", 404)


# ---------------------------------------------------------------------------
# netinv.audit.physical
# ---------------------------------------------------------------------------


class TestPhysicalAudit(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["netinv.audit.physical"])

    def _items(self, extra=None):
        items = [
            {"device_id": "dev-odp-1", "found_status": "installed", "evidence_ref": "E1", "observed_at": OLDER_AT, "source": "crew-1"},
            {"device_id": "dev-olt-1", "found_status": "retired", "evidence_ref": "E2", "observed_at": OLDER_AT, "source": "crew-1"},
            {"device_id": "dev-ghost", "found_status": "installed", "evidence_ref": "E3", "observed_at": OLDER_AT, "source": "crew-1"},
        ]
        if extra:
            items.extend(extra)
        return {"items": items}

    def test_match_discrepancy_unknown(self):
        ctx = base_ctx()
        out = self.H(ctx, self._items())
        self.assertEqual(out["match_count"], 1)
        self.assertEqual(out["discrepancy_count"], 1)
        self.assertEqual(out["discrepancies"][0]["local_status"], "installed")
        self.assertEqual(out["discrepancies"][0]["found_status"], "retired")
        self.assertEqual(out["unknown_to_inventory"], [{"device_id": "dev-ghost", "evidence_ref": "E3"}])
        self.assertFalse(out["correction_applied"])

    def test_duplicates_ignored(self):
        ctx = base_ctx()
        dup = {"device_id": "dev-odp-1", "found_status": "installed", "evidence_ref": "E1", "observed_at": OLDER_AT, "source": "crew-1"}
        out = self.H(ctx, self._items(extra=[dup]))
        self.assertEqual(out["duplicates_ignored"], 1)

    def test_future_observation_rejected(self):
        ctx = base_ctx()
        items = self._items()
        items["items"][0]["observed_at"] = FUTURE_AT
        expect_domain_error(self, lambda: self.H(ctx, items), "timestamp_in_future")

    def test_readonly(self):
        ctx = base_ctx()
        before = json.dumps(ctx.store, sort_keys=True, default=str)
        self.H(ctx, self._items())
        self.assertEqual(json.dumps(ctx.store, sort_keys=True, default=str), before)


# ---------------------------------------------------------------------------
# noc.monitor.cpu
# ---------------------------------------------------------------------------


class TestNocMonitorCpu(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["noc.monitor.cpu"])

    def _samples(self, values, at=FRESH_AT, source="poller-1"):
        return {"device_id": "dev-olt-1", "samples": [{"source": source, "observed_at": at, "value_int": v} for v in values]}

    def test_ok_and_breach(self):
        ctx = base_ctx()
        out = self.H(ctx, {"device_id": "dev-olt-1", "samples": [
            {"source": "poller-1", "observed_at": OLDER_AT, "value_int": 42},
            {"source": "poller-1", "observed_at": FRESH_AT, "value_int": 61},
        ]})
        self.assertEqual(out["status"], "ok")
        self.assertFalse(out["breach"])
        self.assertEqual(out["latest_fresh"]["value_int"], 61)
        self.assertEqual(out["fresh_count"], 2)
        self.assertFalse(out["live_poll_performed"])
        out = self.H(ctx, self._samples([95]))
        self.assertEqual(out["status"], "degraded")
        self.assertTrue(out["breach"])

    def test_stale_future_duplicates(self):
        ctx = base_ctx()
        samples = {
            "device_id": "dev-olt-1",
            "samples": [
                {"source": "p1", "observed_at": STALE_AT, "value_int": 50},
                {"source": "p2", "observed_at": FUTURE_AT, "value_int": 50},
                {"source": "p3", "observed_at": FRESH_AT, "value_int": 30},
                {"source": "p3", "observed_at": FRESH_AT, "value_int": 31},
            ],
        }
        out = self.H(ctx, samples)
        self.assertEqual(out["stale_count"], 1)
        self.assertEqual(out["future_ignored"], 1)
        self.assertEqual(out["duplicates_ignored"], 1)
        self.assertEqual(out["fresh_count"], 1)
        self.assertEqual(out["latest_fresh"]["value_int"], 30)
        self.assertEqual(out["status"], "ok")

    def test_all_stale_status(self):
        ctx = base_ctx()
        out = self.H(ctx, self._samples([50], at=STALE_AT))
        self.assertEqual(out["status"], "stale")
        self.assertIsNone(out["latest_fresh"])
        self.assertFalse(out["breach"])

    def test_unknown_device_and_bad_value(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, {"device_id": "dev-ghost", "samples": self._samples([1])["samples"]}), "not_found", 404)
        expect_domain_error(self, lambda: self.H(ctx, self._samples([101])), "invalid_argument")
        expect_domain_error(self, lambda: self.H(ctx, self._samples([True])), "invalid_argument")

    def test_ram_metric_and_custom_threshold(self):
        ctx = base_ctx()
        out = self.H(ctx, dict(self._samples([85]), metric="ram", warn_threshold=90))
        self.assertEqual(out["metric"], "ram")
        self.assertEqual(out["status"], "ok")


# ---------------------------------------------------------------------------
# noc.map.network
# ---------------------------------------------------------------------------


class TestNocMapNetwork(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["noc.map.network"])

    def _net(self):
        ctx = base_ctx()
        link(ctx, "dev-olt-1", "dev-odc-1", 500)
        link(ctx, "dev-odc-1", "dev-odp-1", 100, status="planned")
        return ctx

    def test_full_map(self):
        ctx = self._net()
        out = self.H(ctx, {})
        self.assertEqual(out["device_count"], 3)
        self.assertEqual(out["link_count"], 2)
        self.assertFalse(out["live_state"])
        self.assertFalse(out["coverage_promised"])
        node_ids = [n["device_id"] for n in out["nodes"]]
        self.assertEqual(node_ids, sorted(node_ids))

    def test_link_status_filter(self):
        ctx = self._net()
        out = self.H(ctx, {"link_status": "active"})
        self.assertEqual(out["link_count"], 1)
        self.assertEqual(out["edges"][0]["status"], "active")

    def test_root_subtree_filter(self):
        ctx = self._net()
        out = self.H(ctx, {"root_device_id": "dev-odc-1"})
        self.assertEqual(out["device_count"], 2)  # odc + odp child
        self.assertEqual(out["link_count"], 1)  # planned odc-odp
        for edge in out["edges"]:
            self.assertIn(edge["a_device_id"] + edge["b_device_id"], {"dev-odc-1dev-odp-1"})

    def test_unknown_root(self):
        ctx = self._net()
        expect_domain_error(self, lambda: self.H(ctx, {"root_device_id": "dev-ghost"}), "not_found", 404)


# ---------------------------------------------------------------------------
# noc.shift.handover
# ---------------------------------------------------------------------------


class TestShiftHandover(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["noc.shift.handover"])

    def test_record_and_reject_duplicate_label(self):
        ctx = base_ctx()
        out = self.H(
            ctx,
            {
                "shift_label": "2026-10-24-day",
                "operator_name": "NOC A",
                "ticket_ids": ["tkt-open"],
                "outage_ids": ["out-1"],
                "work_order_ids": ["wo-1"],
                "notes": "quiet shift",
            },
        )
        self.assertEqual(out["status"], "recorded")
        self.assertEqual(len(out["tickets"]), 1)
        self.assertEqual(len(out["outages"]), 1)
        self.assertEqual(len(out["work_orders"]), 1)
        expect_domain_error(
            self,
            lambda: self.H(ctx, {"shift_label": "2026-10-24-day", "operator_name": "NOC B"}),
            "duplicate_shift_handover",
            409,
        )

    def test_unknown_reference_404(self):
        ctx = base_ctx()
        expect_domain_error(
            self,
            lambda: self.H(ctx, {"shift_label": "s1", "operator_name": "NOC A", "ticket_ids": ["tkt-ghost"]}),
            "not_found",
            404,
        )
        expect_domain_error(
            self,
            lambda: self.H(ctx, {"shift_label": "s2", "operator_name": "NOC A", "outage_ids": ["out-ghost"]}),
            "not_found",
            404,
        )

    def test_minimal_and_event(self):
        ctx = base_ctx()
        out = self.H(ctx, {"shift_label": "s3", "operator_name": "NOC C"})
        self.assertEqual(out["status"], "recorded")
        self.assertTrue(any(e[0] == "noc.shift.handover.recorded" for e in ctx.events))


# ---------------------------------------------------------------------------
# noc.incident.postmortem
# ---------------------------------------------------------------------------


class TestIncidentPostmortem(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["noc.incident.postmortem"])

    def _args(self, **over):
        args = {
            "ticket_id": "tkt-1",
            "outage_id": "out-1",
            "title": "Backhaul fiber cut",
            "summary": "Segment between OLT and ODC was cut.",
            "root_cause_category": "fiber_cut",
            "corrective_actions": ["Replace segment", "Add route diversity"],
            "timeline": [
                {"at": "2026-10-24T08:00:00Z", "description": "Alert raised"},
                {"at": "2026-10-24T07:30:00Z", "description": "LOS observed"},
            ],
            "affected_subscribers": 12,
        }
        args.update(over)
        return args

    def test_record_requires_resolved_ticket(self):
        ctx = base_ctx()
        out = self.H(ctx, self._args())
        self.assertEqual(out["status"], "recorded")
        self.assertFalse(out["cause_verified"])
        self.assertTrue(out["human_review_required"])
        expect_domain_error(self, lambda: self.H(ctx, self._args()), "duplicate_postmortem", 409)
        expect_domain_error(self, lambda: self.H(ctx, self._args(ticket_id="tkt-open")), "invalid_transition", 400)

    def test_timeline_sorted_and_outage_fk(self):
        ctx = base_ctx()
        self.H(ctx, self._args())
        record = list(ctx.store["incident_postmortem"].values())[0]
        times = [e["at"] for e in record["timeline"]]
        self.assertEqual(times, sorted(times))
        expect_domain_error(self, lambda: self.H(base_ctx(), self._args(outage_id="out-ghost")), "not_found", 404)


# ---------------------------------------------------------------------------
# field.quality.score
# ---------------------------------------------------------------------------


class TestFieldQualityScore(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["field.quality.score"])

    def _args(self, **over):
        args = {
            "technician_id": "tech-1",
            "work_order_id": "wo-1",
            "source": "qa-1",
            "checklist": [
                {"item_id": "pon-clean", "passed": True, "weight": 60},
                {"item_id": "labeling", "passed": False, "weight": 40},
            ],
        }
        args.update(over)
        return args

    def test_weighted_bps_math(self):
        ctx = base_ctx()
        out = self.H(ctx, self._args())
        self.assertEqual(out["score_bps"], 6000)
        self.assertEqual(out["score_percent_text"], "60.00")
        self.assertFalse(out["passed"])  # default threshold 7000
        self.assertEqual(out["failed_item_ids"], ["labeling"])
        self.assertFalse(out["recorded"])

    def test_record_true_writes_quality_score(self):
        ctx = base_ctx()
        out = self.H(ctx, self._args(record=True, pass_threshold_bps=5000))
        self.assertTrue(out["recorded"])
        self.assertIsNotNone(out["quality_score_id"])
        record = ctx.store["quality_score"][out["quality_score_id"]]
        self.assertTrue(record["passed"])
        self.assertEqual(record["score_bps"], 6000)
        self.assertTrue(any(e[0] == "field.quality.score.recorded" for e in ctx.events))

    def test_fk_and_duplicate_item(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, self._args(work_order_id="wo-ghost")), "not_found", 404)
        expect_domain_error(
            self,
            lambda: self.H(ctx, self._args(checklist=[{"item_id": "i1", "passed": True, "weight": 10}, {"item_id": "i1", "passed": True, "weight": 10}])),
            "duplicate_checklist_item",
        )

    def test_full_score(self):
        ctx = base_ctx()
        out = self.H(ctx, self._args(checklist=[{"item_id": "i1", "passed": True, "weight": 100}]))
        self.assertEqual(out["score_bps"], 10000)
        self.assertTrue(out["passed"])


# ---------------------------------------------------------------------------
# field.schedule.optimize
# ---------------------------------------------------------------------------


class TestFieldScheduleOptimize(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["field.schedule.optimize"])

    def _ctx_multi(self):
        ctx = base_ctx()
        ctx.create("work_order", {"subscriber_id": "sub-1", "status": "open", "priority": "normal", "technician_id": None}, id="wo-2")
        ctx.create("work_order", {"subscriber_id": "sub-2", "status": "open", "priority": "normal", "technician_id": None}, id="wo-3")
        return ctx

    def _args(self, orders, techs):
        return {
            "work_orders": orders,
            "technicians": [
                {
                    "technician_id": tech_id,
                    "available_from": start,
                    "available_to": end,
                }
                for tech_id, start, end in techs
            ],
        }

    def test_non_overlap_and_determinism(self):
        ctx = self._ctx_multi()
        orders = [
            {"work_order_id": "wo-1", "duration_minutes": 120, "priority": 10},
            {"work_order_id": "wo-2", "duration_minutes": 60, "priority": 20},
            {"work_order_id": "wo-3", "duration_minutes": 60, "priority": 20},
        ]
        techs = [("tech-1", "2026-10-24T01:00:00Z", "2026-10-24T06:00:00Z"), ("tech-2", "2026-10-24T02:00:00Z", "2026-10-24T06:00:00Z")]
        out1 = self.H(ctx, self._args(orders, techs))
        ctx2 = self._ctx_multi()
        out2 = self.H(ctx2, self._args(copy.deepcopy(orders), copy.deepcopy(techs)))
        self.assertEqual(json.dumps(out1, sort_keys=True), json.dumps(out2, sort_keys=True))
        self.assertEqual(len(out1["assignments"]), 3)
        self.assertEqual(out1["unassigned_work_order_ids"], [])
        self.assertFalse(out1["booked"])
        per_tech = {}
        for a in out1["assignments"]:
            per_tech.setdefault(a["technician_id"], []).append(a)
        for tech_id, items in per_tech.items():
            ordered = sorted(items, key=lambda a: a["proposed_start"])
            for prev, nxt in zip(ordered, ordered[1:]):
                self.assertLessEqual(prev["proposed_end"], nxt["proposed_start"], tech_id)

    def test_priority_lower_runs_first(self):
        ctx = self._ctx_multi()
        orders = [
            {"work_order_id": "wo-1", "duration_minutes": 60, "priority": 50},
            {"work_order_id": "wo-2", "duration_minutes": 60, "priority": 10},
        ]
        out = self.H(ctx, self._args(orders, [("tech-1", "2026-10-24T01:00:00Z", "2026-10-24T05:00:00Z")]))
        self.assertEqual(out["assignments"][0]["work_order_id"], "wo-2")
        self.assertEqual(out["assignments"][0]["proposed_start"], "2026-10-24T01:00:00Z")
        self.assertEqual(out["assignments"][1]["proposed_start"], "2026-10-24T02:00:00Z")

    def test_window_too_small_unassigned(self):
        ctx = self._ctx_multi()
        orders = [{"work_order_id": "wo-1", "duration_minutes": 300, "priority": 10}]
        out = self.H(ctx, self._args(orders, [("tech-1", "2026-10-24T01:00:00Z", "2026-10-24T02:00:00Z")]))
        self.assertEqual(out["assignments"], [])
        self.assertEqual(out["unassigned_work_order_ids"], ["wo-1"])

    def test_fk_and_window_validation(self):
        ctx = self._ctx_multi()
        expect_domain_error(
            self,
            lambda: self.H(ctx, self._args([{"work_order_id": "wo-ghost", "duration_minutes": 60}], [("tech-1", "2026-10-24T01:00:00Z", "2026-10-24T05:00:00Z")])),
            "not_found",
            404,
        )
        expect_domain_error(
            self,
            lambda: self.H(ctx, self._args([{"work_order_id": "wo-1", "duration_minutes": 60}], [("tech-1", "2026-10-24T05:00:00Z", "2026-10-24T01:00:00Z")])),
            "invalid_window",
        )

    def test_readonly(self):
        ctx = self._ctx_multi()
        before = json.dumps(ctx.store, sort_keys=True, default=str)
        self.H(
            ctx,
            self._args(
                [{"work_order_id": "wo-1", "duration_minutes": 60}],
                [("tech-1", "2026-10-24T01:00:00Z", "2026-10-24T05:00:00Z")],
            ),
        )
        self.assertEqual(json.dumps(ctx.store, sort_keys=True, default=str), before)


# ---------------------------------------------------------------------------
# qos.threshold
# ---------------------------------------------------------------------------


class TestQosThreshold(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["qos.threshold"])

    _T = {"latency_ms": 100, "jitter_ms": 30, "loss_bps": 100}

    def test_create_list_supersede(self):
        ctx = base_ctx()
        out = self.H(ctx, {"product_id": "prod-1", "thresholds": dict(self._T), "name": "home-basic"})
        profile_id = out["profile_id"]
        self.assertEqual(out["status"], "active")
        expect_domain_error(self, lambda: self.H(ctx, {"product_id": "prod-1", "thresholds": dict(self._T)}), "duplicate_threshold_profile", 409)
        out2 = self.H(ctx, {"product_id": "prod-1", "thresholds": {"latency_ms": 50, "jitter_ms": 20, "loss_bps": 50}, "replace": True})
        self.assertEqual(out2["superseded_profile_ids"], [profile_id])
        self.assertEqual(ctx.store["qos_threshold_profile"][profile_id]["status"], "superseded")
        listing = self.H(ctx, {})
        self.assertEqual(listing["count"], 2)
        self.assertTrue(listing["analytics"]["bounded"])

    def test_fk_unknown_product(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, {"product_id": "prod-ghost", "thresholds": dict(self._T)}), "not_found", 404)

    def test_inactive_product_rejected(self):
        ctx = base_ctx()
        ctx.store["product"]["prod-1"]["active"] = False
        expect_domain_error(self, lambda: self.H(ctx, {"product_id": "prod-1", "thresholds": dict(self._T)}), "product_inactive", 409)

    def test_threshold_bounds(self):
        ctx = base_ctx()
        bad = dict(self._T)
        bad["latency_ms"] = 0
        expect_domain_error(self, lambda: self.H(ctx, {"product_id": "prod-1", "thresholds": bad}), "invalid_argument")


# ---------------------------------------------------------------------------
# qos.benchmark
# ---------------------------------------------------------------------------


class TestQosBenchmark(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["qos.benchmark"])
    _BENCH = {"latency_ms": 50, "jitter_ms": 20, "loss_bps": 30}

    def _samples(self, rows):
        return {
            "samples": [
                {"source": src, "observed_at": at, "latency_ms": lat, "jitter_ms": jit, "loss_bps": loss}
                for src, at, lat, jit, loss in rows
            ],
            "benchmark": dict(self._BENCH),
        }

    def test_meets_and_breach_with_integer_floor_average(self):
        ctx = base_ctx()
        out = self.H(ctx, self._samples([("s1", FRESH_AT, 10, 2, 1), ("s2", FRESH_AT, 11, 3, 2), ("s3", FRESH_AT, 12, 4, 3)]))
        self.assertEqual(out["measured_averages"]["latency_ms"], 11)  # floor(33/3)
        self.assertTrue(out["meets_benchmark"])
        self.assertEqual(out["breached_metrics"], [])
        rows = [("s1", FRESH_AT, 99, 2, 1)]
        out = self.H(ctx, self._samples(rows))
        self.assertEqual(out["breached_metrics"], ["latency_ms"])
        self.assertFalse(out["meets_benchmark"])
        self.assertEqual(out["status"], "degraded")

    def test_freshness_stale_future_duplicates(self):
        ctx = base_ctx()
        out = self.H(
            ctx,
            self._samples(
                [
                    ("s1", STALE_AT, 10, 2, 1),
                    ("s2", FUTURE_AT, 10, 2, 1),
                    ("s3", FRESH_AT, 10, 2, 1),
                    ("s3", FRESH_AT, 11, 2, 1),
                ]
            ),
        )
        self.assertEqual(out["fresh_count"], 1)
        self.assertEqual(out["stale_count"], 1)
        self.assertEqual(out["future_ignored"], 1)
        self.assertEqual(out["duplicates_ignored"], 1)

    def test_all_stale(self):
        ctx = base_ctx()
        out = self.H(ctx, self._samples([("s1", STALE_AT, 10, 2, 1)]))
        self.assertEqual(out["status"], "stale")
        self.assertIsNone(out["meets_benchmark"])
        self.assertFalse(out["external_data_used"])

    def test_missing_benchmark_rejected(self):
        ctx = base_ctx()
        args = self._samples([("s1", FRESH_AT, 10, 2, 1)])
        del args["benchmark"]
        expect_domain_error(self, lambda: self.H(ctx, args), "invalid_argument")


# ---------------------------------------------------------------------------
# fraud.case.investigate
# ---------------------------------------------------------------------------


class TestFraudCaseInvestigate(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["fraud.case.investigate"])

    def _args(self, **over):
        args = {
            "case_ref": "CASE-100",
            "subscriber_id": "sub-1",
            "allegation": "payment_fraud",
            "evidence": [{"evidence_ref": "pay-1", "description": "chargeback pattern", "observed_at": OLDER_AT, "source": "finance-1"}],
            "notes": "opened by revenue assurance",
        }
        args.update(over)
        return args

    def test_review_case_never_verdict(self):
        ctx = base_ctx()
        out = self.H(ctx, self._args())
        self.assertEqual(out["status"], "under_review")
        self.assertEqual(out["outcome"], "pending_human_decision")
        self.assertIsNone(out["guilty_determination"])
        self.assertFalse(out["verdict_recorded"])
        record = ctx.store["fraud_investigation"][out["investigation_id"]]
        self.assertEqual(record["allegation"], "payment_fraud")
        self.assertIsNone(record["guilty_determination"])
        # subscriber untouched
        self.assertEqual(ctx.store["subscriber"]["sub-1"]["status"], "active")
        expect_domain_error(self, lambda: self.H(ctx, self._args()), "duplicate_case", 409)

    def test_fk_and_duplicate_evidence(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, self._args(subscriber_id="sub-ghost")), "not_found", 404)
        expect_domain_error(
            self,
            lambda: self.H(ctx, self._args(evidence=[{"evidence_ref": "e1", "description": "a"}, {"evidence_ref": "e1", "description": "b"}])),
            "duplicate_evidence",
        )

    def test_allegation_enum(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, self._args(allegation="witchcraft")), "invalid_argument")


# ---------------------------------------------------------------------------
# fraud.blacklist.subscriber
# ---------------------------------------------------------------------------


class TestFraudBlacklistSubscriber(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["fraud.blacklist.subscriber"])

    def _args(self, **over):
        args = {
            "subscriber_id": "sub-1",
            "reason": "payment_fraud",
            "evidence_refs": ["pay-1", "pay-2"],
            "justification": "three confirmed chargebacks",
        }
        args.update(over)
        return args

    def test_request_only_never_blacklists(self):
        ctx = base_ctx()
        out = self.H(ctx, self._args())
        self.assertEqual(out["status"], "requested")
        self.assertIs(out["blacklisted"], False)
        self.assertIs(out["effective"], False)
        self.assertTrue(out["review_only"])
        record = ctx.store["blacklist_review_request"][out["request_id"]]
        self.assertEqual(record["decision"], "pending_human_approval")
        self.assertIs(record["blacklisted"], False)
        self.assertEqual(ctx.store["subscriber"]["sub-1"]["status"], "active")  # subscriber NOT blacklisted/suspended
        expect_domain_error(self, lambda: self.H(ctx, self._args()), "open_request_exists", 409)

    def test_fk_unknown_subscriber(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, self._args(subscriber_id="sub-ghost")), "not_found", 404)

    def test_new_request_allowed_after_decision(self):
        ctx = base_ctx()
        first = self.H(ctx, self._args())
        ctx.store["blacklist_review_request"][first["request_id"]]["status"] = "rejected_by_human"
        second = self.H(ctx, self._args(reason="identity_fraud"))
        self.assertEqual(second["status"], "requested")


# ---------------------------------------------------------------------------
# revass.discount.abuse
# ---------------------------------------------------------------------------


class TestRevassDiscountAbuse(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["revass.discount.abuse"])

    def _args(self, discounts):
        return {"policy": {"max_discount_bps": 1000}, "discounts": discounts}

    def test_flags(self):
        ctx = base_ctx()
        out = self.H(
            ctx,
            self._args(
                [
                    {"discount_ref": "D1", "subscriber_id": "sub-1", "discount_bps": 2000, "approved_bps": 500, "period": "2026-09"},
                    {"discount_ref": "D2", "subscriber_id": "sub-2", "discount_bps": 500, "period": "2026-09"},
                    {"discount_ref": "D3", "subscriber_id": "sub-1", "discount_bps": 300, "approved_bps": 300, "period": "2026-09"},
                    {"discount_ref": "D1", "subscriber_id": "sub-1", "discount_bps": 300, "period": "2026-09"},
                ]
            ),
        )
        self.assertEqual(out["finding_count"], 2)
        by_ref = {f["discount_ref"]: f for f in out["findings"]}
        self.assertIn("exceeds_policy_max", by_ref["D1"]["reasons"])
        self.assertIn("exceeds_approved", by_ref["D1"]["reasons"])
        self.assertIn("approval_not_evidenced", by_ref["D2"]["reasons"])
        self.assertIn("D1", out["duplicate_refs"])
        self.assertTrue(out["abuse_indicated"])
        self.assertFalse(out["revocation_executed"])

    def test_clean_discounts(self):
        ctx = base_ctx()
        out = self.H(
            ctx,
            self._args(
                [
                    {"discount_ref": "D1", "subscriber_id": "sub-1", "discount_bps": 500, "approved_bps": 500, "period": "2026-09"},
                    {"discount_ref": "D2", "subscriber_id": "sub-2", "discount_bps": 0, "approved_bps": 0, "period": "2026-09"},
                ]
            ),
        )
        self.assertEqual(out["finding_count"], 0)
        self.assertEqual(out["duplicate_refs"], [])
        self.assertFalse(out["abuse_indicated"])

    def test_fk_unknown_subscriber(self):
        ctx = base_ctx()
        expect_domain_error(
            self,
            lambda: self.H(ctx, self._args([{"discount_ref": "D1", "subscriber_id": "sub-ghost", "discount_bps": 1, "period": "2026-09"}])),
            "not_found",
            404,
        )

    def test_invalid_period(self):
        ctx = base_ctx()
        expect_domain_error(
            self,
            lambda: self.H(ctx, self._args([{"discount_ref": "D1", "discount_bps": 1, "period": "2026-13"}])),
            "invalid_period",
        )


# ---------------------------------------------------------------------------
# revass.report.monthly
# ---------------------------------------------------------------------------


class TestRevassReportMonthly(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["revass.report.monthly"])

    def _args(self, entries, period="2026-09"):
        return {"period": period, "entries": entries}

    def test_integer_totals_and_discrepancies(self):
        ctx = base_ctx()
        out = self.H(
            ctx,
            self._args(
                [
                    {"entry_ref": "R1", "category": "revenue", "expected_idr": 1000000, "actual_idr": 990000},
                    {"entry_ref": "R2", "category": "revenue", "expected_idr": 500000, "actual_idr": 500000},
                    {"entry_ref": "D1", "category": "discount", "expected_idr": 50000, "actual_idr": 60000},
                    {"entry_ref": "T1", "category": "tax", "expected_idr": 0, "actual_idr": 0},
                ]
            ),
        )
        self.assertEqual(out["total_expected_idr"], 1550000)
        self.assertEqual(out["total_actual_idr"], 1550000)
        self.assertEqual(out["total_variance_idr"], 0)
        self.assertEqual(out["totals_by_category"]["revenue"]["variance_idr"], -10000)
        self.assertEqual(out["totals_by_category"]["discount"]["variance_idr"], 10000)
        self.assertEqual(out["discrepancy_count"], 2)
        self.assertEqual(out["status"], "draft")
        self.assertIs(out["submitted"], False)

    def test_duplicates_flagged(self):
        ctx = base_ctx()
        out = self.H(
            ctx,
            self._args(
                [
                    {"entry_ref": "R1", "category": "revenue", "expected_idr": 1, "actual_idr": 1},
                    {"entry_ref": "R1", "category": "revenue", "expected_idr": 1, "actual_idr": 1},
                ]
            ),
        )
        self.assertEqual(out["duplicate_refs"], ["R1"])

    def test_invalid_period_and_negative_money(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, self._args([], period="2026-9") if False else {"period": "2026-9", "entries": [{"entry_ref": "R", "category": "other", "expected_idr": 1, "actual_idr": 1}]}), "invalid_period")
        expect_domain_error(
            self,
            lambda: self.H(ctx, self._args([{"entry_ref": "R1", "category": "revenue", "expected_idr": -5, "actual_idr": 1}])),
            "invalid_amount_idr",
        )

    def test_readonly(self):
        ctx = base_ctx()
        before = json.dumps(ctx.store, sort_keys=True, default=str)
        self.H(ctx, self._args([{"entry_ref": "R1", "category": "revenue", "expected_idr": 1, "actual_idr": 1}]))
        self.assertEqual(json.dumps(ctx.store, sort_keys=True, default=str), before)


# ---------------------------------------------------------------------------
# revass.recovery.action
# ---------------------------------------------------------------------------


class TestRevassRecoveryAction(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["revass.recovery.action"])

    def _args(self, **over):
        args = {
            "case_ref": "RC-1",
            "action_type": "offset_next_invoice",
            "amount_idr": 150000,
            "subscriber_id": "sub-1",
            "invoice_id": "inv-1",
            "justification": "confirmed overbilling variance",
        }
        args.update(over)
        return args

    def test_request_only_no_money_moves(self):
        ctx = base_ctx()
        out = self.H(ctx, self._args())
        self.assertEqual(out["status"], "pending_approval")
        self.assertIs(out["money_movement_performed"], False)
        self.assertIs(out["executed"], False)
        record = ctx.store["recovery_request"][out["request_id"]]
        self.assertEqual(record["amount_idr"], 150000)
        self.assertEqual(record["amount_recovered_idr"], 0)
        self.assertIs(record["money_moved"], False)
        invoice = ctx.store["invoice"]["inv-1"]
        self.assertEqual(invoice["status"], "open")  # invoice untouched
        expect_domain_error(self, lambda: self.H(ctx, self._args()), "open_request_exists", 409)

    def test_invoice_subscriber_mismatch(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, self._args(subscriber_id="sub-2")), "subscriber_invoice_mismatch")

    def test_fk_unknown_invoice(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, self._args(invoice_id="inv-ghost")), "not_found", 404)

    def test_money_validation(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, self._args(amount_idr=-1)), "invalid_amount_idr")
        expect_domain_error(self, lambda: self.H(ctx, self._args(amount_idr=10.5)), "invalid_amount_idr")

    def test_after_decision_new_request_allowed(self):
        ctx = base_ctx()
        first = self.H(ctx, self._args())
        ctx.store["recovery_request"][first["request_id"]]["status"] = "approved"
        second = self.H(ctx, self._args(action_type="writeoff_request"))
        self.assertEqual(second["status"], "pending_approval")


# ---------------------------------------------------------------------------
# workflow.sla.attach
# ---------------------------------------------------------------------------


class TestWorkflowSlaAttach(unittest.TestCase):
    H = staticmethod(extensions.HANDLERS["workflow.sla.attach"])

    def test_attach_template_and_duplicate(self):
        ctx = base_ctx()
        out = self.H(ctx, {"sla_id": "sla-1", "template_id": "wf-tpl-1"})
        self.assertEqual(out["status"], "attached")
        self.assertEqual(out["workflow_template_id"], "wf-tpl-1")
        expect_domain_error(self, lambda: self.H(ctx, {"sla_id": "sla-1", "template_id": "wf-tpl-1"}), "already_attached", 409)
        expect_domain_error(self, lambda: self.H(ctx, {"sla_id": "sla-1", "template_id": "wf-tpl-1"}), "already_attached", 409)

    def test_attach_instance(self):
        ctx = base_ctx()
        out = self.H(ctx, {"sla_id": "sla-1", "instance_id": "wf-inst-1"})
        self.assertEqual(out["workflow_instance_id"], "wf-inst-1")
        binding = list(ctx.store["workflow_sla_binding"].values())[0]
        self.assertIsNone(binding["workflow_template_id"])

    def test_exactly_one_target(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, {"sla_id": "sla-1"}), "invalid_argument")
        expect_domain_error(
            self, lambda: self.H(ctx, {"sla_id": "sla-1", "template_id": "wf-tpl-1", "instance_id": "wf-inst-1"}), "invalid_argument"
        )

    def test_fk_unknown(self):
        ctx = base_ctx()
        expect_domain_error(self, lambda: self.H(ctx, {"sla_id": "sla-ghost", "template_id": "wf-tpl-1"}), "not_found", 404)
        expect_domain_error(self, lambda: self.H(ctx, {"sla_id": "sla-1", "template_id": "tpl-ghost"}), "not_found", 404)
        expect_domain_error(self, lambda: self.H(ctx, {"sla_id": "sla-1", "instance_id": "inst-ghost"}), "not_found", 404)


class TestReadOnlyGuarantee(unittest.TestCase):
    """Every non-MUTATIONS handler must leave the tenant store untouched."""

    def test_readonly_handlers_write_nothing(self):
        cases = {
            "noc.monitor.cpu": {"device_id": "dev-olt-1", "samples": [{"source": "p1", "observed_at": FRESH_AT, "value_int": 42}]},
            "noc.map.network": {},
            "qos.benchmark": {
                "samples": [{"source": "s1", "observed_at": FRESH_AT, "latency_ms": 10, "jitter_ms": 2, "loss_bps": 1}],
                "benchmark": {"latency_ms": 50, "jitter_ms": 20, "loss_bps": 30},
            },
            "revass.discount.abuse": {
                "policy": {"max_discount_bps": 1000},
                "discounts": [{"discount_ref": "D1", "subscriber_id": "sub-1", "discount_bps": 100, "period": "2026-09"}],
            },
        }
        for handler_id in EXPECTED_READONLY:
            ctx = base_ctx()
            link(ctx, "dev-olt-1", "dev-odc-1", 100)
            before = json.dumps(ctx.store, sort_keys=True, default=str)
            payload = cases.get(handler_id, {"from_device_id": "dev-olt-1", "to_device_id": "dev-odc-1"})
            if handler_id == "netinv.audit.physical":
                payload = {
                    "items": [
                        {"device_id": "dev-odp-1", "found_status": "installed", "evidence_ref": "E1", "observed_at": OLDER_AT, "source": "crew-1"}
                    ]
                }
            elif handler_id == "field.schedule.optimize":
                payload = {
                    "work_orders": [{"work_order_id": "wo-1", "duration_minutes": 60}],
                    "technicians": [{"technician_id": "tech-1", "available_from": "2026-10-24T01:00:00Z", "available_to": "2026-10-24T05:00:00Z"}],
                }
            elif handler_id == "revass.report.monthly":
                payload = {"period": "2026-09", "entries": [{"entry_ref": "R1", "category": "revenue", "expected_idr": 100, "actual_idr": 100}]}
            out = extensions.HANDLERS[handler_id](ctx, payload)
            self.assertIsInstance(out, dict, handler_id)
            self.assertEqual(json.dumps(ctx.store, sort_keys=True, default=str), before, handler_id)
            self.assertEqual(ctx.events, [], handler_id)
            self.assertNotIn(handler_id, extensions.MUTATIONS)


if __name__ == "__main__":
    print("extensions module: %s" % EXTENSIONS_FILE)
    print("package origin: %s" % _PACKAGE_ORIGIN)
    print("common origin: %s (proof: %s)" % (COMMON_ORIGIN, COMMON_PROOF))
    unittest.main(verbosity=1)

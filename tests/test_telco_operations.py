"""Tests for the OPERATIONS worker module backend/telco_engine/operations.py.

Run from the worker worktree:  python3 -m unittest tests.test_telco_operations -v

Loading strategy (per CONTRACT.txt):
- If the root checkout /Users/id/Projects/harmonika-tui already contains
  backend/telco_engine/common.py, the REAL common is used for the import.
- Otherwise a local contract-faithful stub of common is injected so the
  module can be exercised before root publishes common.py. The root repo is
  never modified.
All fixtures are synthetic; no customer PII. Uses unittest only.
"""

import json
import re
import sys
import types
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.telco_engine import operations as ops
USED_REAL_ROOT_COMMON = True


# ---------------------------------------------------------------------------
# FakeContext: implements the ctx interface from CONTRACT.txt (single tenant).
# ---------------------------------------------------------------------------


class FakeContext:
    tenant = "tenant_test"
    actor = "worker_test"

    def __init__(self, now="2026-10-24T12:00:00Z"):
        self.now = now
        self.store = {}
        self.events = []

    def _kind_store(self, kind, create=True):
        if kind != kind.lower() or not kind.replace("_", "").isalnum():
            raise ops.DomainError("invalid_kind", 400)
        if kind not in self.store:
            if not create:
                return {}
            self.store[kind] = {}
        return self.store[kind]

    def create(self, kind, data, id=None):
        store = self._kind_store(kind)
        record_id = id or uuid.uuid4().hex
        if record_id in store:
            raise ops.DomainError("duplicate", 409)
        record = {"id": record_id, "version": 1, "created_at": self.now, "updated_at": self.now}
        record.update(data)
        store[record_id] = record
        return dict(record)

    def get(self, kind, record_id):
        record = self._kind_store(kind, create=False).get(record_id)
        if record is None:
            raise ops.DomainError("not_found", 404)
        return dict(record)

    def update(self, kind, record_id, changes, expected_version=None):
        store = self._kind_store(kind, create=False)
        record = store.get(record_id)
        if record is None:
            raise ops.DomainError("not_found", 404)
        if expected_version is not None and expected_version != record["version"]:
            raise ops.DomainError("conflict", 409)
        for protected in ("id", "version", "created_at"):
            if protected in changes:
                raise ops.DomainError("protected_field", 400)
        record.update(changes)
        record["version"] += 1
        record["updated_at"] = self.now
        return dict(record)

    def list(self, kind, where=None, limit=1000):
        records = [dict(r) for r in self._kind_store(kind, create=False).values()]
        if where:
            records = [r for r in records if all(r.get(k) == v for k, v in where.items())]
        return records[: max(0, min(limit, 1000))]

    def event(self, event_type, data):
        self.events.append((event_type, data))


def _dt(minute=0, hour=12, day=24):
    return "2026-10-%02dT%02d:%02d:00Z" % (day, hour, minute)


def _sample(value, observed_at, source="probe-1"):
    return {"value_int": value, "observed_at": observed_at, "source": source}


def _iso_shift(base="2026-10-24T12:00:00Z", minutes=0, hour=None, minute=None, day=None):
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if hour is not None or minute is not None or day is not None:
        dt = dt.replace(
            hour=dt.hour if hour is None else hour,
            minute=dt.minute if minute is None else minute,
            day=dt.day if day is None else day,
        )
    dt = dt.fromtimestamp(dt.timestamp() + minutes * 60, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_subscriber(ctx, subscriber_id="sub-0001", status="active"):
    return ctx.create("subscriber", {"name": "Synthetic Sub", "status": status}, id=subscriber_id)


def _seed_product(ctx, product_id="prod-0001", active=True):
    return ctx.create(
        "product", {"name": "Synthetic 20M", "price_idr": 250000, "active": active}, id=product_id
    )


# ---------------------------------------------------------------------------
# Static contract invariants
# ---------------------------------------------------------------------------


class TestContractInvariants(unittest.TestCase):
    def test_every_handler_has_strict_schema(self):
        self.assertEqual(set(ops.HANDLERS), set(ops.SCHEMAS))
        for handler_id, schema in ops.SCHEMAS.items():
            self.assertEqual(schema.get("type"), "object", handler_id)
            self.assertIs(schema.get("additionalProperties"), False, handler_id)
            self.assertIsInstance(schema.get("properties"), dict, handler_id)
            for required_key in schema.get("required", []):
                self.assertIn(required_key, schema["properties"], handler_id)

    def test_mutations_subset_of_handlers(self):
        self.assertTrue(set(ops.MUTATIONS).issubset(set(ops.HANDLERS)))

    def test_handler_count_in_contract_band(self):
        self.assertGreaterEqual(len(ops.HANDLERS), 30)
        self.assertLessEqual(len(ops.HANDLERS), 52)

    def test_owned_catalog_ids_fully_accounted(self):
        self.assertEqual(len(ops.CATALOG_IDS_OWNED), 72)
        implemented = set(ops.HANDLERS)
        unavailable = implemented & set(ops.CATALOG_IDS_OWNED)
        self.assertEqual(unavailable, implemented)
        absent = set(ops.CATALOG_IDS_OWNED) - implemented
        self.assertEqual(absent, set(ops.LIMITATIONS) & absent)
        for absent_id in absent:
            self.assertIn(absent_id, ops.LIMITATIONS, absent_id)

    def test_mutating_ids_declared(self):
        expected_mutations = {
            "netinv.device.register", "netinv.device.update", "netinv.device.decommission",
            "netinv.port.management", "netinv.link.management", "netinv.olt.port.alloc",
            "netinv.spare.parts", "prov.order.create", "prov.order.validate",
            "prov.allocate.resource", "prov.workorder.create", "prov.workorder.complete",
            "noc.alert.threshold", "noc.alert.fiber.cut", "noc.alert.outage",
            "noc.ticket.create", "noc.ticket.assign", "field.dispatch.create",
            "field.dispatch.assign", "field.arrival.track", "field.work.complete",
            "field.photo.upload", "field.signature.capture", "field.equipment.use",
            "field.time.track", "field.reassignment", "field.emergency.dispatch",
            "field.feedback.collect", "sla.define", "sla.breach.alert",
            "trouble.ticket.create", "trouble.ticket.escalate", "trouble.ticket.resolve",
        }
        self.assertEqual(set(ops.MUTATIONS), expected_mutations)

    def test_no_float_types_in_schemas(self):
        def walk(node):
            if isinstance(node, dict):
                self.assertNotEqual(node.get("type"), "number")
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for schema in ops.SCHEMAS.values():
            walk(schema)

    def test_handlers_return_json_serializable(self):
        ctx = FakeContext()
        result = ops.HANDLERS["noc.escalation.matrix"](ctx, {})
        json.dumps(result)
        result = ops.HANDLERS["noc.dashboard.realtime"](ctx, {})
        json.dumps(result)

    def test_common_source_reported(self):
        self.assertTrue(USED_REAL_ROOT_COMMON or "backend.telco_engine.common" in sys.modules)


# ---------------------------------------------------------------------------
# Engine 1: network inventory
# ---------------------------------------------------------------------------


class TestInventory(unittest.TestCase):
    def test_device_lifecycle_and_transitions(self):
        ctx = FakeContext()
        created = ops.HANDLERS["netinv.device.register"](
            ctx, {"name": "OLT-JKT-01", "device_type": "olt", "site": "JKT-01", "capacity_ports": 16}
        )
        device_id = created["device_id"]
        self.assertEqual(created["status"], "planned")
        ops.HANDLERS["netinv.device.update"](ctx, {"device_id": device_id, "status": "installed"})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.device.update"](ctx, {"device_id": device_id, "status": "planned"})
        ops.HANDLERS["netinv.device.update"](ctx, {"device_id": device_id, "status": "retired"})
        final = ctx.get("device", device_id)
        self.assertEqual(final["status"], "retired")

    def test_hierarchy_cycle_detection(self):
        ctx = FakeContext()
        a = ops.HANDLERS["netinv.device.register"](ctx, {"name": "A", "device_type": "router"})["device_id"]
        b = ops.HANDLERS["netinv.device.register"](ctx, {"name": "B", "device_type": "switch", "parent_id": a})["device_id"]
        c = ops.HANDLERS["netinv.device.register"](ctx, {"name": "C", "device_type": "switch"})["device_id"]
        ops.HANDLERS["netinv.device.update"](ctx, {"device_id": a, "parent_id": c})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.device.update"](ctx, {"device_id": b, "parent_id": b})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.device.update"](ctx, {"device_id": c, "parent_id": b})
        built = ops.HANDLERS["netinv.hierarchy.build"](ctx, {})
        self.assertEqual(built["count"], 3)
        depths = {n["device_id"]: n["depth"] for n in built["nodes"]}
        self.assertEqual(depths[c], 0)
        self.assertEqual(depths[a], 1)
        self.assertEqual(depths[b], 2)

    def test_port_management_duplicate_and_capacity(self):
        ctx = FakeContext()
        device_id = ops.HANDLERS["netinv.device.register"](
            ctx, {"name": "OLT-02", "device_type": "olt"}
        )["device_id"]
        first = ops.HANDLERS["netinv.port.management"](
            ctx, {"device_id": device_id, "ports": [{"port_code": "1/1/1"}, {"port_code": "1/1/2"}]}
        )
        self.assertEqual(first["count"], 2)
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.port.management"](ctx, {"device_id": device_id, "ports": [{"port_code": "1/1/1"}]})
        capacity = ops.HANDLERS["netinv.capacity.track"](ctx, {"device_id": device_id})
        self.assertEqual(capacity["devices"][0]["total"], 2)
        self.assertEqual(capacity["devices"][0]["available"], 2)
        self.assertEqual(capacity["devices"][0]["utilization_bps"], 0)

    def test_links_trace_and_duplicate(self):
        ctx = FakeContext()
        ids = [
            ops.HANDLERS["netinv.device.register"](ctx, {"name": name, "device_type": "switch"})["device_id"]
            for name in ("CORE", "DIST-1", "DIST-2", "ODP-9")
        ]
        core, dist1, dist2, odp9 = ids
        ops.HANDLERS["netinv.link.management"](ctx, {"a_device_id": core, "b_device_id": dist1, "length_m": 100})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.link.management"](ctx, {"a_device_id": dist1, "b_device_id": core, "length_m": 100})
        ops.HANDLERS["netinv.link.management"](ctx, {"a_device_id": dist1, "b_device_id": dist2, "length_m": 200})
        ops.HANDLERS["netinv.link.management"](ctx, {"a_device_id": dist2, "b_device_id": odp9, "length_m": 50})
        trace = ops.HANDLERS["netinv.cable.trace"](ctx, {"from_device_id": core, "to_device_id": odp9})
        self.assertTrue(trace["reachable"])
        self.assertEqual(trace["path_device_ids"], [core, dist1, dist2, odp9])
        self.assertEqual(trace["total_length_m"], 350)
        orphan = ops.HANDLERS["netinv.device.register"](ctx, {"name": "ISLAND", "device_type": "router"})["device_id"]
        missing = ops.HANDLERS["netinv.cable.trace"](ctx, {"from_device_id": core, "to_device_id": orphan})
        self.assertFalse(missing["reachable"])
        self.assertEqual(missing["path_device_ids"], [])

    def test_spatial_view_haversine_and_coverage_note(self):
        ctx = FakeContext()
        jakarta = ops.HANDLERS["netinv.device.register"](
            ctx, {"name": "OLT-JKT", "device_type": "olt", "lat_micro": -6120000, "lon_micro": 106820000}
        )["device_id"]
        bogor = ops.HANDLERS["netinv.device.register"](
            ctx, {"name": "OLT-BGR", "device_type": "olt", "lat_micro": -6595000, "lon_micro": 106816000}
        )["device_id"]
        ops.HANDLERS["netinv.device.register"](ctx, {"name": "NO-COORD", "device_type": "odp"})
        near = ops.HANDLERS["netinv.spatial.view"](
            ctx, {"center_lat_micro": -6120000, "center_lon_micro": 106820000, "radius_m": 5000}
        )
        self.assertEqual([d["device_id"] for d in near["devices"]], [jakarta])
        far = ops.HANDLERS["netinv.spatial.view"](
            ctx, {"center_lat_micro": -6120000, "center_lon_micro": 106820000, "radius_m": 100000}
        )
        self.assertEqual(far["count"], 2)
        jkt_to_bgr = [d for d in far["devices"] if d["device_id"] == bogor][0]["distance_m"]
        self.assertTrue(40000 < jkt_to_bgr < 70000, jkt_to_bgr)
        self.assertIn("does not promise service coverage", near["note"])

    def test_olt_port_alloc_conflict_and_flow(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        _seed_subscriber(ctx, subscriber_id="sub-0002")
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-3", "device_type": "olt"})["device_id"]
        created = ops.HANDLERS["netinv.port.management"](ctx, {"device_id": device_id, "ports": [{"port_code": "p1"}]})
        resource_id = created["created"][0]
        ops.HANDLERS["netinv.olt.port.alloc"](ctx, {"resource_id": resource_id, "subscriber_id": "sub-0001"})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.olt.port.alloc"](ctx, {"resource_id": resource_id, "subscriber_id": "sub-0002"})
        self.assertEqual(ctx.get("resource", resource_id)["subscriber_id"], "sub-0001")

    def test_decommission_blocks_assigned_resources(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-DC", "device_type": "olt"})["device_id"]
        child = ops.HANDLERS["netinv.device.register"](
            ctx, {"name": "ODP-DC", "device_type": "odp", "parent_id": device_id}
        )["device_id"]
        created = ops.HANDLERS["netinv.port.management"](ctx, {"device_id": device_id, "ports": [{"port_code": "p1"}]})
        resource_id = created["created"][0]
        ops.HANDLERS["netinv.olt.port.alloc"](ctx, {"resource_id": resource_id, "subscriber_id": "sub-0001"})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.device.decommission"](ctx, {"device_id": device_id, "reason": "old"})
        ctx.update("resource", resource_id, {"status": "available", "subscriber_id": None})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.device.decommission"](ctx, {"device_id": device_id})
        ctx.update("device", child, {"status": "retired"})
        done = ops.HANDLERS["netinv.device.decommission"](ctx, {"device_id": device_id, "reason": "eol"})
        self.assertEqual(done["status"], "retired")

    def test_utilization_track_ranks_fresh_evidence(self):
        ctx = FakeContext()
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-UT", "device_type": "olt"})["device_id"]
        result = ops.HANDLERS["netinv.utilization.track"](
            ctx,
            {
                "device_id": device_id,
                "interfaces": [
                    _sample(9200, _iso_shift(minutes=-3), "if-eth0"),
                    _sample(9200, _iso_shift(minutes=-3), "if-eth0"),
                    _sample(4000, _iso_shift(minutes=-2), "if-eth1"),
                    _sample(9999, _iso_shift(minutes=-99999), "if-eth2"),
                ],
                "max_age_s": 3600,
            },
        )
        self.assertEqual(len(result["interfaces_fresh"]), 2)
        self.assertEqual(result["interfaces_fresh"][0]["source"], "if-eth0")
        self.assertEqual(result["device_avg_bps"], 6600)
        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(result["duplicates_ignored"], 1)
        self.assertTrue(result["evidence_based"])

    def test_spare_parts_adjust_reject_negative(self):
        ctx = FakeContext()
        ops.HANDLERS["netinv.spare.parts"](ctx, {"sku": "ONT-ZTE-F660", "name": "ONT", "qty": 10})
        updated = ops.HANDLERS["netinv.spare.parts"](ctx, {"sku": "ONT-ZTE-F660", "adjust_delta": -4})
        self.assertEqual(updated["part"]["qty"], 6)
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.spare.parts"](ctx, {"sku": "ONT-ZTE-F660", "adjust_delta": -7})
        listing = ops.HANDLERS["netinv.spare.parts"](ctx, {})
        self.assertEqual(listing["count"], 1)


# ---------------------------------------------------------------------------
# Engine 2: provisioning (local planning only)
# ---------------------------------------------------------------------------


class TestProvisioning(unittest.TestCase):
    def _order_with_ports(self, ctx, ports=("1/1/1", "1/1/2"), order_id=None):
        _seed_subscriber(ctx)
        _seed_product(ctx)
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-P", "device_type": "olt"})["device_id"]
        ops.HANDLERS["netinv.port.management"](
            ctx, {"device_id": device_id, "ports": [{"port_code": p} for p in ports]}
        )
        args = {
            "subscriber_id": "sub-0001",
            "product_id": "prod-0001",
            "required_resources": [{"resource_type": "olt_port", "count": 2}],
        }
        if order_id:
            args["__id"] = order_id
        order = ops.HANDLERS["prov.order.create"](ctx, args)
        return order["provision_order_id"]

    def test_order_create_rejects_terminated_subscriber(self):
        ctx = FakeContext()
        _seed_subscriber(ctx, status="terminated")
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["prov.order.create"](ctx, {"subscriber_id": "sub-0001"})

    def test_validate_requires_active_product_and_resources(self):
        ctx = FakeContext()
        order_id = self._order_with_ports(ctx)
        ops.HANDLERS["prov.order.validate"](ctx, {"provision_order_id": order_id})
        self.assertEqual(ctx.get("provision_order", order_id)["status"], "validated")
        ctx2 = FakeContext()
        _seed_subscriber(ctx2)
        _seed_product(ctx2, active=True)
        order2 = ops.HANDLERS["prov.order.create"](ctx2, {"subscriber_id": "sub-0001", "product_id": "prod-0001"})
        ctx2.update("product", "prod-0001", {"active": False})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["prov.order.validate"](ctx2, {"provision_order_id": order2["provision_order_id"]})
        self.assertEqual(ctx2.get("provision_order", order2["provision_order_id"])["status"], "draft")
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["prov.order.create"](ctx2, {"subscriber_id": "sub-0001", "product_id": "prod-0001"})

    def test_allocate_atomic_no_partial_when_insufficient(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-AT", "device_type": "olt"})["device_id"]
        ops.HANDLERS["netinv.port.management"](ctx, {"device_id": device_id, "ports": [{"port_code": "only-1"}]})
        order = ops.HANDLERS["prov.order.create"](ctx, {"subscriber_id": "sub-0001"})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["prov.allocate.resource"](
                ctx, {"provision_order_id": order["provision_order_id"], "resource_type": "olt_port", "count": 2}
            )
        available = ctx.list("resource", where={"resource_type": "olt_port", "status": "available"})
        self.assertEqual(len(available), 1)
        self.assertEqual(ctx.get("provision_order", order["provision_order_id"])["status"], "draft")

    def test_two_orders_cannot_reserve_same_port(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        _seed_subscriber(ctx, subscriber_id="sub-0002")
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-RACE", "device_type": "olt"})["device_id"]
        ops.HANDLERS["netinv.port.management"](ctx, {"device_id": device_id, "ports": [{"port_code": "r1"}]})
        order_a = ops.HANDLERS["prov.order.create"](ctx, {"subscriber_id": "sub-0001"})
        order_b = ops.HANDLERS["prov.order.create"](ctx, {"subscriber_id": "sub-0002"})
        res_a = ops.HANDLERS["prov.allocate.resource"](
            ctx, {"provision_order_id": order_a["provision_order_id"], "resource_type": "olt_port", "count": 1}
        )
        self.assertEqual(len(res_a["allocated_resource_ids"]), 1)
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["prov.allocate.resource"](
                ctx, {"provision_order_id": order_b["provision_order_id"], "resource_type": "olt_port", "count": 1}
            )
        reserved = ctx.list("resource", where={"status": "reserved"})
        self.assertEqual(len(reserved), 1)

    def test_allocate_idempotent_when_requirement_satisfied(self):
        ctx = FakeContext()
        order_id = self._order_with_ports(ctx)
        first = ops.HANDLERS["prov.allocate.resource"](
            ctx, {"provision_order_id": order_id, "resource_type": "olt_port", "count": 2}
        )
        self.assertEqual(len(first["allocated_resource_ids"]), 2)
        self.assertEqual(ctx.get("provision_order", order_id)["status"], "resources_reserved")
        again = ops.HANDLERS["prov.allocate.resource"](
            ctx, {"provision_order_id": order_id, "resource_type": "olt_port", "count": 2}
        )
        self.assertIn("already satisfied", again["note"])
        self.assertEqual(len(again["allocated_resource_ids"]), 2)

    def test_workorder_complete_requires_assignment_and_evidence(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        wo = ops.HANDLERS["prov.workorder.create"](ctx, {"subscriber_id": "sub-0001"})
        wo_id = wo["work_order_id"]
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["prov.workorder.complete"](ctx, {"work_order_id": wo_id, "note": "done"})
        ops.HANDLERS["prov.workorder.create"](ctx, {"subscriber_id": "sub-0001", "technician_id": "tech-andi"})
        wo2 = ctx.list("work_order")[1]
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["prov.workorder.complete"](ctx, {"work_order_id": wo2["id"], "note": ""})
        done = ops.HANDLERS["prov.workorder.complete"](
            ctx, {"work_order_id": wo2["id"], "note": "installed ONT", "evidence_refs": ["photo-1"]}
        )
        self.assertEqual(done["service_activation"], "not_performed_requires_adapter")
        self.assertEqual(ctx.get("work_order", wo2["id"])["status"], "completed")


# ---------------------------------------------------------------------------
# Engine 3: NOC evidence handling
# ---------------------------------------------------------------------------


class TestNoc(unittest.TestCase):
    def test_uptime_freshness_duplicates_and_future(self):
        ctx = FakeContext()
        samples = [
            _sample(1, _iso_shift(minutes=-10), "nms-a"),
            _sample(1, _iso_shift(minutes=-10), "nms-a"),
            _sample(0, _iso_shift(minutes=-5), "nms-b"),
            _sample(1, _iso_shift(minutes=30), "nms-future"),
            _sample(1, _iso_shift(minutes=-7200), "nms-old"),
        ]
        result = ops.HANDLERS["noc.monitor.uptime"](ctx, {"device_id": "olt-1", "samples": samples})
        self.assertEqual(result["samples_total"], 5)
        self.assertEqual(result["duplicates_ignored"], 1)
        self.assertEqual(result["future_ignored"], 1)
        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(result["fresh_count"], 2)
        self.assertEqual(result["uptime_bps"], 5000)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["latest_state"], "down")

    def test_uptime_stale_only_is_stale(self):
        ctx = FakeContext()
        result = ops.HANDLERS["noc.monitor.uptime"](
            ctx,
            {"device_id": "olt-1", "samples": [_sample(1, _iso_shift(minutes=-7200))], "max_age_s": 60},
        )
        self.assertEqual(result["status"], "stale")
        self.assertIsNone(result["uptime_bps"])

    def test_bandwidth_threshold_breach(self):
        ctx = FakeContext()
        result = ops.HANDLERS["noc.monitor.bandwidth"](
            ctx,
            {
                "device_id": "olt-1",
                "samples": [_sample(950, _iso_shift(minutes=-2))],
                "warn_threshold": 900,
            },
        )
        self.assertTrue(result["breach"])
        self.assertEqual(result["status"], "degraded")

    def test_optical_low_direction(self):
        ctx = FakeContext()
        result = ops.HANDLERS["noc.monitor.optical"](
            ctx,
            {
                "device_id": "olt-1",
                "samples": [_sample(-2800, _iso_shift(minutes=-1))],
                "warn_threshold": -2700,
            },
        )
        self.assertTrue(result["breach"])
        fresh_ok = ops.HANDLERS["noc.monitor.optical"](
            ctx,
            {
                "device_id": "olt-1",
                "samples": [_sample(-2200, _iso_shift(minutes=-1))],
                "warn_threshold": -2700,
            },
        )
        self.assertFalse(fresh_ok["breach"])

    def test_alert_threshold_creates_record_and_dedups(self):
        ctx = FakeContext()
        samples = [_sample(80, _iso_shift(minutes=-2), "nms-a")]
        first = ops.HANDLERS["noc.alert.threshold"](
            ctx, {"device_id": "olt-1", "metric_name": "temperature_c", "samples": samples, "warn_threshold": 60}
        )
        self.assertIsNotNone(first["alert_id"])
        second = ops.HANDLERS["noc.alert.threshold"](
            ctx, {"device_id": "olt-1", "metric_name": "temperature_c", "samples": samples, "warn_threshold": 60}
        )
        self.assertEqual(second["alert_id"], first["alert_id"])
        self.assertIn("existing alert", second["note"])
        no_breach = ops.HANDLERS["noc.alert.threshold"](
            ctx, {"device_id": "olt-1", "metric_name": "temperature_c", "samples": [_sample(40, _iso_shift(minutes=-2))], "warn_threshold": 60}
        )
        self.assertIsNone(no_breach["alert_id"])

    def test_fiber_cut_creates_outage_once(self):
        ctx = FakeContext()
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-FC", "device_type": "olt"})["device_id"]
        first = ops.HANDLERS["noc.alert.fiber.cut"](
            ctx, {"device_id": device_id, "observed_at": _iso_shift(minutes=-5), "source": "olt-log"}
        )
        self.assertFalse(first["duplicate"])
        second = ops.HANDLERS["noc.alert.fiber.cut"](
            ctx, {"device_id": device_id, "observed_at": _iso_shift(minutes=-3), "source": "olt-log"}
        )
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["outage_id"], first["outage_id"])
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["noc.alert.fiber.cut"](
                ctx,
                {"device_id": device_id, "observed_at": _iso_shift(minutes=-5000), "source": "olt-log", "max_age_s": 60},
            )

    def test_mass_outage_correlation_rules(self):
        ctx = FakeContext()
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-MASS", "device_type": "olt"})["device_id"]
        observations_single_sub = [
            {"subscriber_id": "sub-%d" % i, "observed_at": _iso_shift(minutes=-5), "source": "callcenter"}
            for i in (1, 1, 1)
        ]
        not_declared = ops.HANDLERS["noc.alert.outage"](
            ctx, {"asset_id": device_id, "observations": observations_single_sub}
        )
        self.assertFalse(not_declared["declared"])
        self.assertEqual(not_declared["reason"], "insufficient_independent_observations")
        self.assertEqual(not_declared["duplicates_ignored"], 2)
        observations_multi = [
            {"subscriber_id": "sub-%d" % i, "observed_at": _iso_shift(minutes=-4 - i), "source": "callcenter"}
            for i in (1, 2, 3)
        ]
        declared = ops.HANDLERS["noc.alert.outage"](ctx, {"asset_id": device_id, "observations": observations_multi})
        self.assertTrue(declared["declared"])
        self.assertEqual(declared["distinct_subscribers"], 3)
        repeat = ops.HANDLERS["noc.alert.outage"](ctx, {"asset_id": device_id, "observations": observations_multi})
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(repeat["outage_id"], declared["outage_id"])

    def test_ticket_create_assign_flow(self):
        ctx = FakeContext()
        ctx.create('device',{'name':'Fixture','device_type':'olt','status':'installed'},id='olt-9')
        ticket = ops.HANDLERS["noc.ticket.create"](ctx, {"title": "LOS on OLT-9", "severity": "P1", "device_id": "olt-9"})
        ticket_id = ticket["ticket_id"]
        self.assertEqual(ticket["status"], "open")
        assigned = ops.HANDLERS["noc.ticket.assign"](ctx, {"ticket_id": ticket_id, "assignee": "noc-eng-7"})
        self.assertEqual(assigned["status"], "assigned")
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["noc.ticket.assign"](ctx, {"ticket_id": "missing-ticket", "assignee": "x"})

    def test_escalation_matrix_shape(self):
        ctx = FakeContext()
        matrix = ops.HANDLERS["noc.escalation.matrix"](ctx, {})["matrix"]
        self.assertEqual(set(matrix), {"P1", "P2", "P3"})
        self.assertLess(matrix["P1"]["response_minutes"], matrix["P2"]["response_minutes"])

    def test_dashboard_counts(self):
        ctx = FakeContext()
        ops.HANDLERS["noc.ticket.create"](ctx, {"title": "t1", "severity": "P1"})
        ops.HANDLERS["noc.ticket.create"](ctx, {"title": "t2", "severity": "P3"})
        ops.HANDLERS["noc.ticket.create"](ctx, {"title": "t3", "severity": "P3"})
        ops.HANDLERS["netinv.device.register"](ctx, {"name": "d", "device_type": "olt", "status": "installed"})
        dash = ops.HANDLERS["noc.dashboard.realtime"](ctx, {})
        self.assertEqual(dash["tickets_open_by_severity"], {"P1": 1, "P2": 0, "P3": 2})
        self.assertEqual(dash["outages_open"], 0)
        self.assertEqual(dash["devices_installed"], 1)


# ---------------------------------------------------------------------------
# Engine 11: field operations
# ---------------------------------------------------------------------------


class TestField(unittest.TestCase):
    def test_dispatch_create_assign_nearest(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        wo = ops.HANDLERS["field.dispatch.create"](
            ctx,
            {
                "subscriber_id": "sub-0001",
                "site_lat_micro": -6120000,
                "site_lon_micro": 106820000,
                "required_skills": ["gpon", "splicing"],
            },
        )
        wo_id = wo["work_order_id"]
        assigned = ops.HANDLERS["field.dispatch.assign"](
            ctx,
            {
                "work_order_id": wo_id,
                "candidates": [
                    {"technician_id": "tech-far", "lat_micro": -6610000, "lon_micro": 107420000},
                    {"technician_id": "tech-near", "lat_micro": -6121000, "lon_micro": 106821000},
                ],
            },
        )
        self.assertEqual(assigned["technician_id"], "tech-near")
        self.assertEqual(assigned["assign_method"], "nearest_heuristic")
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.dispatch.assign"](ctx, {"work_order_id": wo_id, "technician_id": "tech-x"})
        explicit = ops.HANDLERS["field.dispatch.create"](ctx, {"subscriber_id": "sub-0001"})
        assigned2 = ops.HANDLERS["field.dispatch.assign"](
            ctx, {"work_order_id": explicit["work_order_id"], "technician_id": "tech-andi"}
        )
        self.assertEqual(assigned2["assign_method"], "explicit")

    def test_route_optimize_order_and_total(self):
        ctx = FakeContext()
        result = ops.HANDLERS["field.route.optimize"](
            ctx,
            {
                "start_lat_micro": -6120000,
                "start_lon_micro": 106820000,
                "stops": [
                    {"stop_id": "far-stop", "lat_micro": -6300000, "lon_micro": 106950000},
                    {"stop_id": "near-stop", "lat_micro": -6121000, "lon_micro": 106821000},
                ],
            },
        )
        self.assertEqual(result["stop_order"], ["near-stop", "far-stop"])
        self.assertEqual(len(result["legs"]), 2)
        self.assertEqual(
            result["total_distance_m"], sum(leg["distance_m"] for leg in result["legs"])
        )
        self.assertTrue(result["heuristic"])

    def test_arrival_track_sets_in_progress_once(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        wo = ops.HANDLERS["field.dispatch.create"](ctx, {"subscriber_id": "sub-0001"})
        wo_id = wo["work_order_id"]
        ops.HANDLERS["field.dispatch.assign"](ctx, {"work_order_id": wo_id, "technician_id": "tech-andi"})
        arrival = ops.HANDLERS["field.arrival.track"](
            ctx, {"work_order_id": wo_id, "observed_at": _iso_shift(minutes=-2), "source": "tech-app"}
        )
        self.assertEqual(arrival["status"], "in_progress")
        self.assertTrue(arrival["arrival"]["fresh"])
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.arrival.track"](
                ctx, {"work_order_id": wo_id, "observed_at": _iso_shift(minutes=-1), "source": "tech-app"}
            )

    def test_complete_requires_assignment_then_feedback_and_signature(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        wo = ops.HANDLERS["field.dispatch.create"](ctx, {"subscriber_id": "sub-0001"})
        wo_id = wo["work_order_id"]
        ops.HANDLERS["field.dispatch.assign"](ctx, {"work_order_id": wo_id, "technician_id": "tech-andi"})
        ops.HANDLERS["field.arrival.track"](
            ctx, {"work_order_id": wo_id, "observed_at": _iso_shift(minutes=-30), "source": "tech-app"}
        )
        done = ops.HANDLERS["field.work.complete"](
            ctx, {"work_order_id": wo_id, "note": "install ok", "evidence_refs": ["snap-1", "snap-2"]}
        )
        self.assertEqual(done["status"], "completed")
        photo = ops.HANDLERS["field.photo.upload"](
            ctx, {"work_order_id": wo_id, "photo_ref": "photo-inst-9", "taken_at": _iso_shift(minutes=-20)}
        )
        self.assertEqual(photo["work_order_id"], wo_id)
        sig = ops.HANDLERS["field.signature.capture"](
            ctx,
            {"work_order_id": wo_id, "signature_ref": "sig-ref-1", "signed_by": "Synthetic Person", "signed_at": _iso_shift(minutes=-19)},
        )
        self.assertEqual(sig["work_order_id"], wo_id)
        feedback = ops.HANDLERS["field.feedback.collect"](ctx, {"work_order_id": wo_id, "rating": 5, "comment": "fast"})
        self.assertEqual(feedback["rating"], 5)
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.feedback.collect"](ctx, {"work_order_id": wo_id, "rating": 1})

    def test_reassignment_history(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        wo = ops.HANDLERS["field.dispatch.create"](ctx, {"subscriber_id": "sub-0001"})
        wo_id = wo["work_order_id"]
        ops.HANDLERS["field.dispatch.assign"](ctx, {"work_order_id": wo_id, "technician_id": "tech-a"})
        moved = ops.HANDLERS["field.reassignment"](
            ctx, {"work_order_id": wo_id, "new_technician_id": "tech-b", "reason": "tech-a sick"}
        )
        self.assertEqual(moved["technician_id"], "tech-b")
        self.assertEqual(ctx.get("work_order", wo_id)["reassignments"][0]["from"], "tech-a")
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.reassignment"](
                ctx, {"work_order_id": wo_id, "new_technician_id": "tech-b", "reason": "same again"}
            )

    def test_equipment_use_decrements_stock(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        ops.HANDLERS["netinv.spare.parts"](ctx, {"sku": "ONT-X", "name": "ONT X", "qty": 3})
        wo = ops.HANDLERS["field.dispatch.create"](ctx, {"subscriber_id": "sub-0001"})
        wo_id = wo["work_order_id"]
        ops.HANDLERS["field.dispatch.assign"](ctx, {"work_order_id": wo_id, "technician_id": "tech-a"})
        used = ops.HANDLERS["field.equipment.use"](ctx, {"work_order_id": wo_id, "sku": "ONT-X", "qty": 2})
        self.assertEqual(used["remaining_qty"], 1)
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.equipment.use"](ctx, {"work_order_id": wo_id, "sku": "ONT-X", "qty": 2})
        stock = ctx.list("spare_part", where={"sku": "ONT-X"})[0]
        self.assertEqual(stock["qty"], 1)

    def test_time_track_totals(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        wo = ops.HANDLERS["field.dispatch.create"](ctx, {"subscriber_id": "sub-0001"})
        wo_id = wo["work_order_id"]
        first = ops.HANDLERS["field.time.track"](
            ctx,
            {
                "work_order_id": wo_id,
                "technician_id": "tech-a",
                "started_at": _iso_shift(minutes=-60),
                "ended_at": _iso_shift(minutes=-30),
            },
        )
        self.assertEqual(first["duration_s"], 1800)
        second = ops.HANDLERS["field.time.track"](
            ctx,
            {
                "work_order_id": wo_id,
                "technician_id": "tech-a",
                "started_at": _iso_shift(minutes=-20),
                "ended_at": _iso_shift(minutes=-10),
            },
        )
        self.assertEqual(second["work_order_total_s"], 2400)
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.time.track"](
                ctx,
                {
                    "work_order_id": wo_id,
                    "technician_id": "tech-a",
                    "started_at": _iso_shift(minutes=10),
                    "ended_at": _iso_shift(minutes=5),
                },
            )

    def test_emergency_dispatch_requires_open_outage(self):
        ctx = FakeContext()
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-EM", "device_type": "olt"})["device_id"]
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.emergency.dispatch"](ctx, {"device_id": device_id})
        outage = ops.HANDLERS["noc.alert.fiber.cut"](
            ctx, {"device_id": device_id, "observed_at": _iso_shift(minutes=-5), "source": "olt-log"}
        )
        dispatch = ops.HANDLERS["field.emergency.dispatch"](
            ctx, {"outage_id": outage["outage_id"], "required_skills": ["splicing"]}
        )
        self.assertEqual(dispatch["priority"], "emergency")
        self.assertEqual(dispatch["source_outage_id"], outage["outage_id"])

    def test_skill_match_ranking(self):
        ctx = FakeContext()
        result = ops.HANDLERS["field.skill.match"](
            ctx,
            {
                "required_skills": ["gpon", "splicing"],
                "candidates": [
                    {"technician_id": "tech-1", "skills": ["gpon"]},
                    {"technician_id": "tech-2", "skills": ["GPON", "Splicing", "fiber"]},
                    {"technician_id": "tech-3", "skills": ["fiber"]},
                ],
            },
        )
        ranking = result["ranking"]
        self.assertEqual(ranking[0]["technician_id"], "tech-2")
        self.assertEqual(ranking[0]["score"], 2)
        self.assertEqual(ranking[1]["technician_id"], "tech-1")
        self.assertEqual(ranking[2]["missing"], ["gpon", "splicing"])


# ---------------------------------------------------------------------------
# Engine 12: SLA, QoS, trouble tickets
# ---------------------------------------------------------------------------


class TestAssurance(unittest.TestCase):
    SLA_ARGS = {
        "name": "standard-2026",
        "targets": {
            "P1": {"response_minutes": 15, "resolution_minutes": 240},
            "P2": {"response_minutes": 60, "resolution_minutes": 1440},
            "P3": {"response_minutes": 240, "resolution_minutes": 4320},
        },
        "penalty_bps_per_hour": 100,
        "penalty_cap_pct": 20,
    }

    def test_sla_define_and_monitor_breach_math(self):
        ctx = FakeContext()
        sla = ops.HANDLERS["sla.define"](ctx, self.SLA_ARGS)
        sla_id = sla["sla_id"]
        ticket = ops.HANDLERS["trouble.ticket.create"](ctx, {"title": "slow", "severity": "P2"})
        ticket_id = ticket["ticket_id"]
        ctx.now = _iso_shift(minutes=90)
        monitored = ops.HANDLERS["sla.monitor"](ctx, {"sla_id": sla_id, "ticket_ids": [ticket_id]})
        evaluation = monitored["evaluations"][0]
        self.assertTrue(evaluation["response"]["breached"])
        self.assertTrue(evaluation["response"]["pending"])
        self.assertEqual(evaluation["response"]["pending_minutes"], 90)
        ctx.now = _iso_shift(minutes=200)
        ops.HANDLERS["trouble.ticket.resolve"](
            ctx, {"ticket_id": ticket_id, "note": "router rebooted", "evidence_refs": ["log-1"]}
        )
        resolved_eval = ops.HANDLERS["sla.monitor"](ctx, {"sla_id": sla_id, "ticket_ids": [ticket_id]})
        final = resolved_eval["evaluations"][0]
        self.assertEqual(final["resolution"]["actual_minutes"], 200)
        self.assertFalse(final["resolution"]["breached"])

    def test_sla_breach_alert_idempotent(self):
        ctx = FakeContext()
        sla_id = ops.HANDLERS["sla.define"](ctx, self.SLA_ARGS)["sla_id"]
        ticket_id = ops.HANDLERS["trouble.ticket.create"](ctx, {"title": "down", "severity": "P1"})["ticket_id"]
        ctx.now = _iso_shift(minutes=120)
        first = ops.HANDLERS["sla.breach.alert"](ctx, {"sla_id": sla_id, "ticket_ids": [ticket_id]})
        self.assertEqual(len(first["alert_ids"]), 1)
        second = ops.HANDLERS["sla.breach.alert"](ctx, {"sla_id": sla_id, "ticket_ids": [ticket_id]})
        self.assertEqual(second["alert_ids"], [])
        self.assertEqual(second["already_alerted_ticket_ids"], [ticket_id])

    def test_penalty_calc_exact_integer(self):
        ctx = FakeContext()
        sla_args = {
            "name": "penalty-test",
            "targets": {
                "P1": {"response_minutes": 15, "resolution_minutes": 240},
                "P2": {"response_minutes": 15, "resolution_minutes": 60},
                "P3": {"response_minutes": 240, "resolution_minutes": 4320},
            },
            "penalty_bps_per_hour": 100,
            "penalty_cap_pct": 20,
        }
        sla_id = ops.HANDLERS["sla.define"](ctx, sla_args)["sla_id"]
        ticket_id = ops.HANDLERS["trouble.ticket.create"](ctx, {"title": "down", "severity": "P2"})["ticket_id"]
        ctx.now = _iso_shift(minutes=120)
        ops.HANDLERS["trouble.ticket.resolve"](ctx, {"ticket_id": ticket_id, "note": "fixed"})
        result = ops.HANDLERS["sla.penalty.calc"](
            ctx, {"sla_id": sla_id, "ticket_id": ticket_id, "monthly_charge_idr": 1000000}
        )
        self.assertEqual(result["breach_minutes"], 120 - 60)
        self.assertEqual(result["penalty_idr"], 1000000 * 100 * 60 // (10000 * 60))
        self.assertEqual(result["penalty_idr"], 10000)
        self.assertTrue(result["calculation_only"])
        not_breached_ticket = ops.HANDLERS["trouble.ticket.create"](ctx, {"title": "flap", "severity": "P2"})["ticket_id"]
        ops.HANDLERS["trouble.ticket.resolve"](ctx, {"ticket_id": not_breached_ticket, "note": "ok"})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["sla.penalty.calc"](
                ctx, {"sla_id": sla_id, "ticket_id": not_breached_ticket, "monthly_charge_idr": 1000000}
            )

    def test_penalty_cap_applies(self):
        ctx = FakeContext()
        sla_args = dict(self.SLA_ARGS)
        sla_args["penalty_bps_per_hour"] = 1000
        sla_args["penalty_cap_pct"] = 5
        sla_id = ops.HANDLERS["sla.define"](ctx, sla_args)["sla_id"]
        ticket_id = ops.HANDLERS["trouble.ticket.create"](ctx, {"title": "down", "severity": "P1"})["ticket_id"]
        ctx.now = _iso_shift(minutes=600)
        ops.HANDLERS["trouble.ticket.resolve"](ctx, {"ticket_id": ticket_id, "note": "fiber spliced"})
        result = ops.HANDLERS["sla.penalty.calc"](
            ctx, {"sla_id": sla_id, "ticket_id": ticket_id, "monthly_charge_idr": 1000000}
        )
        self.assertTrue(result["capped"])
        self.assertEqual(result["penalty_idr"], 50000)

    def test_sla_report_customer_union_of_overlapping_outages(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-R", "device_type": "olt"})["device_id"]
        ctx.create('resource',{'subscriber_id':'sub-0001','device_id':device_id,'resource_type':'olt_port','status':'assigned','value':'1/1/1'})
        cut1 = ops.HANDLERS["noc.alert.fiber.cut"](
            ctx,
            {
                "device_id": device_id,
                "observed_at": _iso_shift(hour=10, minute=0),
                "source": "olt-log",
                "max_age_s": 10800,
            },
        )
        outage1 = ctx.get("outage", cut1["outage_id"])
        ctx.create(
            "outage",
            {
                "asset_kind": "device",
                "asset_id": device_id,
                "status": "resolved",
                "severity": "P1",
                "cause": "fiber_cut_los",
                "started_at": _iso_shift(hour=11, minute=0),
                "ended_at": _iso_shift(hour=13, minute=0),
                "alert_id": None,
                "correlation": None,
            },
        )
        ctx.update("outage", outage1["id"], {"ended_at": _iso_shift(hour=12, minute=0), "status": "resolved"})
        ctx.now = _iso_shift(hour=15)
        report = ops.HANDLERS["sla.report.customer"](
            ctx,
            {
                "subscriber_id": "sub-0001",
                "period_start": _iso_shift(hour=8, minute=0),
                "period_end": _iso_shift(hour=14, minute=0),
                "sla_id": ops.HANDLERS["sla.define"](ctx, self.SLA_ARGS)["sla_id"],
            },
        )
        self.assertEqual(report["downtime_s_union"], 3 * 3600)
        window_s = 6 * 3600
        self.assertEqual(report["availability_bps"], (window_s - 3 * 3600) * 10000 // window_s)
        self.assertEqual(report["outage_intervals_merged"], 1)
        self.assertTrue(report["calculation_only"])

    def test_qos_measure_averaging_and_breaches(self):
        ctx = FakeContext()
        samples = [
            {"latency_ms": 120, "jitter_ms": 10, "loss_bps": 50, "observed_at": _iso_shift(minutes=-4), "source": "probe-1"},
            {"latency_ms": 140, "jitter_ms": 20, "loss_bps": 60, "observed_at": _iso_shift(minutes=-3), "source": "probe-2"},
            {"latency_ms": 999, "jitter_ms": 10, "loss_bps": 10, "observed_at": _iso_shift(minutes=-2), "source": "probe-1"},
        ]
        result = ops.HANDLERS["qos.measure"](ctx, {"subscriber_id": "sub-0001", "samples": samples})
        self.assertEqual(result["fresh_count"], 3)
        self.assertEqual(result["avg_latency_ms"], 419)
        self.assertEqual(result["avg_jitter_ms"], 13)
        self.assertEqual(result["avg_loss_bps"], 40)
        self.assertIn("latency_ms", result["breached_metrics"])
        self.assertEqual(result["status"], "degraded")
        ok = ops.HANDLERS["qos.measure"](
            ctx,
            {
                "subscriber_id": "sub-0001",
                "samples": [dict(s, latency_ms=50) for s in samples],
                "thresholds": {"latency_ms": 500, "jitter_ms": 100, "loss_bps": 200},
            },
        )
        self.assertEqual(ok["status"], "ok")
        stale = ops.HANDLERS["qos.measure"](
            ctx,
            {
                "subscriber_id": "sub-0001",
                "samples": [dict(s, observed_at=_iso_shift(minutes=-7200)) for s in samples],
                "max_age_s": 60,
            },
        )
        self.assertEqual(stale["status"], "stale")

    def test_trouble_ticket_escalate_and_resolve(self):
        ctx = FakeContext()
        ticket_id = ops.HANDLERS["trouble.ticket.create"](ctx, {"title": "no sync", "severity": "P3"})["ticket_id"]
        escalated = ops.HANDLERS["trouble.ticket.escalate"](ctx, {"ticket_id": ticket_id, "reason": "aging"})
        self.assertEqual(escalated["severity"], "P2")
        again = ops.HANDLERS["trouble.ticket.escalate"](ctx, {"ticket_id": ticket_id, "reason": "still aging"})
        self.assertEqual(again["severity"], "P1")
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["trouble.ticket.escalate"](ctx, {"ticket_id": ticket_id, "reason": "more"})
        resolved = ops.HANDLERS["trouble.ticket.resolve"](
            ctx, {"ticket_id": ticket_id, "note": "port resealed", "evidence_refs": ["photo-p"]}
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["external_verification"], "not_performed")
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["trouble.ticket.resolve"](ctx, {"ticket_id": ticket_id, "note": "again"})


# ---------------------------------------------------------------------------
# Validation/boundary behaviour
# ---------------------------------------------------------------------------


class TestBoundaries(unittest.TestCase):
    def test_rejects_path_like_refs_and_bad_types(self):
        ctx = FakeContext()
        _seed_subscriber(ctx)
        wo = ops.HANDLERS["field.dispatch.create"](ctx, {"subscriber_id": "sub-0001"})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.photo.upload"](
                ctx, {"work_order_id": wo["work_order_id"], "photo_ref": "/etc/passwd", "taken_at": _iso_shift(minutes=-1)}
            )
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.photo.upload"](
                ctx, {"work_order_id": wo["work_order_id"], "photo_ref": "ok-ref", "taken_at": "not-a-date"}
            )
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["field.feedback.collect"](ctx, {"work_order_id": wo["work_order_id"], "rating": True})

    def test_rejects_future_evidence(self):
        ctx = FakeContext()
        result = ops.HANDLERS["noc.monitor.temperature"](
            ctx, {"device_id": "olt-x", "samples": [_sample(50, _iso_shift(minutes=5))]}
        )
        self.assertEqual(result["future_ignored"], 1)
        self.assertEqual(result["status"], "stale")
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["noc.alert.fiber.cut"](
                ctx, {"device_id": "olt-x", "observed_at": _iso_shift(minutes=5), "source": "olt-log"}
            )

    def test_events_written_by_mutations_only_reachable_paths(self):
        ctx = FakeContext()
        device_id = ops.HANDLERS["netinv.device.register"](ctx, {"name": "OLT-E", "device_type": "olt"})["device_id"]
        ops.HANDLERS["noc.alert.outage"](
            ctx,
            {
                "asset_id": device_id,
                "observations": [
                    {"subscriber_id": "sub-%d" % i, "observed_at": _iso_shift(minutes=-5), "source": "cc"}
                    for i in (1, 2, 3)
                ],
            },
        )
        self.assertTrue(any(t == "noc.alert.outage" for t, _ in ctx.events))

    def test_identifier_guard_blocks_slash_and_empty(self):
        ctx = FakeContext()
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.capacity.track"](ctx, {"device_id": ""})
        with self.assertRaises(ops.DomainError):
            ops.HANDLERS["netinv.capacity.track"](ctx, {"device_id": "../olt"})

    def test_queries_do_not_write_records(self):
        ctx = FakeContext()
        before = json.dumps(ctx.store, sort_keys=True, default=str)
        ops.HANDLERS["netinv.hierarchy.build"](ctx, {})
        ops.HANDLERS["netinv.spatial.view"](ctx, {"center_lat_micro": 0, "center_lon_micro": 0, "radius_m": 100})
        ops.HANDLERS["netinv.capacity.track"](ctx, {})
        ops.HANDLERS["noc.escalation.matrix"](ctx, {})
        ops.HANDLERS["noc.dashboard.realtime"](ctx, {})
        ops.HANDLERS["field.skill.match"](
            ctx, {"required_skills": ["gpon"], "candidates": [{"technician_id": "t1", "skills": ["gpon"]}]}
        )
        ops.HANDLERS["field.route.optimize"](
            ctx,
            {
                "start_lat_micro": 0,
                "start_lon_micro": 0,
                "stops": [{"stop_id": "s1", "lat_micro": 1000, "lon_micro": 1000}],
            },
        )
        after = json.dumps(ctx.store, sort_keys=True, default=str)
        self.assertEqual(before, after)
        self.assertEqual(ctx.events, [])

    def test_unknown_args_rejected_by_schema_contract(self):
        for handler_id, schema in ops.SCHEMAS.items():
            properties = schema["properties"]
            self.assertTrue(
                all(key in properties for key in schema.get("required", [])),
                handler_id,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TimeAndAllocationRegression(unittest.TestCase):
    def test_chronological_sort_offsets_and_fractional_seconds(self):
        ctx=FakeContext(now='2026-10-24T12:00:10Z')
        args={'device_id':'fixture','samples':[{'source':'new','observed_at':'2026-10-24T12:00:05.900Z','value_int':1},{'source':'old','observed_at':'2026-10-24T12:00:05Z','value_int':0}]}
        self.assertEqual(ops.h_noc_monitor_uptime(ctx,args)['latest_state'],'up')
    def test_fractional_future_sample_is_not_current_evidence(self):
        ctx=FakeContext(now='2026-10-24T12:00:10Z');report=ops._fresh_samples(ctx,[{'source':'future','observed_at':'2026-10-24T12:00:10.100Z','value_int':1}],3600)
        self.assertEqual(report['future_ignored'],1);self.assertFalse(report['fresh'])
    def test_same_instant_different_timezone_not_duplicate_evidence(self):
        ctx=FakeContext();report=ops._fresh_samples(ctx,[{'source':'p','observed_at':'2026-10-24T11:59:00Z','value_int':1},{'source':'p','observed_at':'2026-10-24T18:59:00+07:00','value_int':1}],3600)
        self.assertEqual(len(report['fresh']),1);self.assertEqual(report['duplicates_ignored'],1)
    def test_port_capacity_and_foreign_subscriber_order_guard(self):
        ctx=FakeContext();_seed_subscriber(ctx);_seed_subscriber(ctx,subscriber_id='other')
        device=ops.h_netinv_device_register(ctx,{'name':'oneport','device_type':'olt','capacity_ports':1})['device_id']
        with self.assertRaises(ops.DomainError):ops.h_netinv_port_management(ctx,{'device_id':device,'ports':[{'port_code':'a'},{'port_code':'b'}]})
        port=ops.h_netinv_port_management(ctx,{'device_id':device,'ports':[{'port_code':'a'}]})['created'][0]
        order=ops.h_prov_order_create(ctx,{'subscriber_id':'other'})['provision_order_id']
        with self.assertRaises(ops.DomainError):ops.h_netinv_olt_port_alloc(ctx,{'subscriber_id':'sub-0001','resource_id':port,'provision_order_id':order})
        self.assertEqual(ctx.get('resource',port)['status'],'available')
    def test_customer_sla_excludes_unrelated_outage_and_historical_now(self):
        ctx=FakeContext(now='2026-10-24T12:00:00Z');_seed_subscriber(ctx)
        dev=ops.h_netinv_device_register(ctx,{'name':'unrelated','device_type':'olt'})['device_id']
        ctx.create('outage',{'asset_kind':'device','asset_id':dev,'status':'resolved','started_at':'2026-10-24T09:00:00Z','ended_at':'2026-10-24T10:00:00Z'})
        sla=ctx.create('sla',{'name':'Fixture','targets':{s:{'response_minutes':15,'resolution_minutes':60} for s in ('P1','P2','P3')}})['id']
        ctx.create('ticket',{'subscriber_id':'sub-0001','severity':'P2','status':'open','opened_at':'2026-10-24T09:30:00Z','assigned_at':None,'resolved_at':None})
        r=ops.h_sla_report_customer(ctx,{'subscriber_id':'sub-0001','period_start':'2026-10-24T09:00:00Z','period_end':'2026-10-24T10:00:00Z','sla_id':sla})
        self.assertEqual(r['downtime_s_union'],0);self.assertEqual(r['unattributed_outages_ignored'],1);self.assertEqual(r['sla_evaluations'][0]['response']['pending_minutes'],30)
    def test_mass_outage_cannot_reference_nonexistent_resource(self):
        ctx=FakeContext()
        with self.assertRaises(ops.DomainError):ops.h_noc_alert_outage(ctx,{'asset_id':'ghost','asset_kind':'resource','observations':[{'subscriber_id':'x','observed_at':'2026-10-24T11:55:00Z','source':'fixture'}]})
    def test_retire_update_cannot_bypass_decommission_guard(self):
        ctx=FakeContext();dev=ops.h_netinv_device_register(ctx,{'name':'busy','device_type':'olt'})['device_id']
        ctx.create('resource',{'device_id':dev,'status':'reserved','resource_type':'olt_port','value':'x'})
        with self.assertRaises(ops.DomainError):ops.h_netinv_device_update(ctx,{'device_id':dev,'status':'retired'})
        with self.assertRaises(ops.DomainError):ops.h_netinv_device_decommission(ctx,{'device_id':dev})
        self.assertEqual(ctx.get('device',dev)['status'],'planned')
    def test_cross_metric_alerts_do_not_suppress_each_other(self):
        ctx=FakeContext();a={'device_id':'fixture','samples':[{'source':'p','observed_at':'2026-10-24T11:59:00Z','value_int':99}],'warn_threshold':80}
        one=ops.h_noc_alert_threshold(ctx,{**a,'metric_name':'cpu'});two=ops.h_noc_alert_threshold(ctx,{**a,'metric_name':'temperature'})
        self.assertNotEqual(one['alert_id'],two['alert_id'])
    def test_colon_port_codes_are_matched_in_full(self):
        ctx=FakeContext();dev=ops.h_netinv_device_register(ctx,{'name':'fixture','device_type':'olt'})['device_id']
        ops.h_netinv_port_management(ctx,{'device_id':dev,'ports':[{'port_code':'1:1'}]})
        ops.h_netinv_port_management(ctx,{'device_id':dev,'ports':[{'port_code':'1'}]})
        with self.assertRaises(ops.DomainError):ops.h_netinv_port_management(ctx,{'device_id':dev,'ports':[{'port_code':'1:1'}]})
    def test_leaf_first_deep_topology_has_no_python_recursion(self):
        ctx=FakeContext();ids=['node'+str(i) for i in range(1000)]
        for i,id in enumerate(ids):ctx.create('device',{'name':id,'device_type':'odp','status':'planned','parent_id':ids[i+1] if i+1<len(ids) else None},id=id)
        r=ops.h_netinv_hierarchy_build(ctx,{});self.assertEqual(r['count'],1000);self.assertEqual(r['nodes'][0]['depth'],999)
    def test_owned_reservation_assign_release_is_complete_local_lifecycle(self):
        ctx=FakeContext();_seed_subscriber(ctx)
        dev=ops.h_netinv_device_register(ctx,{'name':'Fixture','device_type':'olt'})['device_id']
        ops.h_netinv_port_management(ctx,{'device_id':dev,'ports':[{'port_code':'1'}]})
        order=ops.h_prov_order_create(ctx,{'subscriber_id':'sub-0001','required_resources':[{'resource_type':'olt_port','count':1}]})['provision_order_id']
        with self.assertRaises(ops.DomainError):ops.h_prov_allocate_resource(ctx,{'provision_order_id':order,'resource_type':'olt_port','count':2})
        port=ops.h_prov_allocate_resource(ctx,{'provision_order_id':order,'resource_type':'olt_port','count':1})['allocated_resource_ids'][0]
        with self.assertRaises(ops.DomainError):ops.h_netinv_olt_port_alloc(ctx,{'resource_id':port,'subscriber_id':'sub-0001'})
        a={'resource_id':port,'subscriber_id':'sub-0001','provision_order_id':order}
        result=ops.h_netinv_olt_port_alloc(ctx,a);self.assertEqual(result['status'],'assigned');self.assertFalse(result['network_configuration_changed'])
        self.assertEqual(ctx.get('provision_order',order)['status'],'awaiting_connector')
        result=ops.h_netinv_olt_port_alloc(ctx,{**a,'action':'release'});self.assertEqual(result['status'],'available');self.assertEqual(ctx.get('provision_order',order)['status'],'draft')
    def test_active_links_block_retirement_until_local_deactivation(self):
        ctx=FakeContext();ids=[ops.h_netinv_device_register(ctx,{'name':str(i),'device_type':'olt'})['device_id'] for i in range(2)]
        result=ops.h_netinv_link_management(ctx,{'a_device_id':ids[0],'b_device_id':ids[1]});link=ctx.list('network_link')[0]['id']
        with self.assertRaises(ops.DomainError):ops.h_netinv_device_decommission(ctx,{'device_id':ids[0]})
        ops.h_netinv_link_management(ctx,{'link_id':link,'status':'inactive'})
        self.assertEqual(ops.h_netinv_device_decommission(ctx,{'device_id':ids[0]})['status'],'retired')

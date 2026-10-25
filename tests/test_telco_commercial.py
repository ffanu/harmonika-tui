"""Commercial telco engine tests (COMMERCIAL worker scope).

Loads backend/telco_engine/commercial.py against the root-provided common.py
(/Users/id/Projects/harmonika-tui) when readable, otherwise an equivalent local
stub, plus a lightweight FakeContext mirroring the root SQLite ctx interface
(kind validation, reserved fields, CAS versioning, bounded lists, tenant get).
Unittest + synthetic data only; no customer PII, no network, no root writes.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import types
import unittest
import uuid
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.normpath(os.path.join(HERE, "..", "backend", "telco_engine", "commercial.py"))
ROOT_CATALOG_PATH = str(Path(__file__).resolve().parents[1]/'backend/telco_engine/catalog_source.json')
from backend.telco_engine import commercial
from backend.telco_engine.common import DomainError
COMMON_ORIGIN = {'value': commercial.__file__.replace('commercial.py','common.py')}

ABSENT_IDS = [
    "bill.invoice.send",
    "prepaid.expire.notify",
    "portal.customer.login",
    "portal.payment.online",
    "portal.speedtest",
    "nps.survey.send",
]

FORBIDDEN_ARG_NAMES = {"path", "file", "command", "url", "host", "port", "ssh", "endpoint"}


class FakeContext(object):
    _KIND_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
    _RESERVED = {"id", "version", "created_at", "updated_at", "tenant", "actor"}

    def __init__(self, now="2026-10-24T10:00:00Z", tenant="tenant-a", actor="commercial-tester"):
        self.now = now
        self.tenant = tenant
        self.actor = actor
        self.store = {}
        self.events = []

    def _kind(self, kind):
        if not (type(kind) is str and self._KIND_RE.fullmatch(kind)):
            raise DomainError("invalid_kind")
        return kind

    def create(self, kind, data, id=None):
        self._kind(kind)
        if not isinstance(data, dict) or (set(data) & self._RESERVED):
            raise DomainError("reserved_fields")
        record_id = id or uuid.uuid4().hex
        bucket = self.store.setdefault(kind, {})
        if record_id in bucket:
            raise DomainError("duplicate_id", 409)
        record = {"id": record_id, "version": 1, "created_at": self.now, "updated_at": self.now}
        record.update(data)
        bucket[record_id] = record
        return dict(record)

    def get(self, kind, record_id):
        self._kind(kind)
        record = self.store.get(kind, {}).get(record_id)
        if record is None:
            raise DomainError("not_found", 404)
        return dict(record)

    def update(self, kind, record_id, changes, expected_version=None):
        self._kind(kind)
        record = self.store.get(kind, {}).get(record_id)
        if record is None:
            raise DomainError("not_found", 404)
        if not isinstance(changes, dict) or (set(changes) & self._RESERVED):
            raise DomainError("reserved_fields")
        if expected_version is not None:
            if not (type(expected_version) is int and record["version"] == expected_version):
                raise DomainError("version_conflict", 409)
        record.update(changes)
        record["version"] += 1
        record["updated_at"] = self.now
        return dict(record)

    def list(self, kind, where=None, limit=1000):
        self._kind(kind)
        rows = [dict(row) for row in self.store.get(kind, {}).values()]
        if where:
            rows = [row for row in rows if all(row.get(key) == value for key, value in where.items())]
        return rows[:limit]

    def event(self, event_type, data):
        self.events.append({"type": event_type, "data": data})
        return None


def make_ctx(now="2026-10-24T10:00:00Z"):
    return FakeContext(now=now)


def run(ctx, skill, args):
    return commercial.HANDLERS[skill](ctx, args)


def expect_error(test, skill, args, code, status=None):
    ctx = test.ctx if hasattr(test, "ctx") else None
    try:
        run(ctx, skill, args)
    except DomainError as error:
        test.assertEqual(error.code, code, "expected %s got %s" % (code, error.code))
        if status is not None:
            test.assertEqual(error.status, status)
        return error
    test.fail("expected DomainError %s" % code)


def mk_product(ctx, name="Home 20Mbps", price=250000, **extra):
    args = {"name": name, "price_idr": price}
    args.update(extra)
    return run(ctx, "product.create", args)["product"]


def mk_sub(ctx, name="Test Subscriber", **extra):
    args = {"name": name}
    args.update(extra)
    return run(ctx, "sub.create", args)["subscriber"]


def activate(ctx, subscriber_id):
    return run(ctx, "sub.status.manage",
               {"subscriber_id": subscriber_id, "new_status": "active", "local_activation_confirmed": True})


def attach_package(ctx, subscriber_id, product_id):
    ctx.update("subscriber", subscriber_id, {"package_id": product_id})


def mk_invoice(ctx, subscriber_id, product_id, period="2026-10", due_at="2026-10-25T00:00:00Z", price=None):
    if price is not None:
        run(ctx, "product.pricing.set", {"product_id": product_id, "price_idr": price, "reason": "test"})
    return run(ctx, "bill.invoice.generate",
               {"subscriber_id": subscriber_id, "product_id": product_id, "period": period, "due_at": due_at})["invoice"]


def mk_verified_payment(ctx, subscriber_id, amount, reference="VERIF-1"):
    return ctx.create("payment", {
        "subscriber_id": subscriber_id,
        "amount_idr": amount,
        "provider_reference": reference,
        "channel": "bank_transfer",
        "evidence": {"bank_ref": "BNK-1"},
        "status": "verified",
        "verified_by": "fixture_payment_adapter",
        "matched_invoice_ids": [],
        "allocated_idr": 0,
    })


class RegistryContractTest(unittest.TestCase):
    def test_registry_shape(self):
        self.assertEqual(set(commercial.HANDLERS), set(commercial.SCHEMAS))
        self.assertTrue(set(commercial.MUTATIONS) <= set(commercial.HANDLERS))
        self.assertGreaterEqual(len(commercial.HANDLERS), 50)

    def test_absent_ids_not_handlers_but_documented(self):
        for skill in ABSENT_IDS:
            self.assertNotIn(skill, commercial.HANDLERS)
            self.assertIn(skill, commercial.LIMITATIONS)
            self.assertIn("NOT IMPLEMENTED", commercial.LIMITATIONS[skill])

    def test_readonly_handlers_not_in_mutations(self):
        readonly = [
            "bill.arrears.calculate", "bill.late.fee", "bill.prorate.calculate",
            "bill.statement.generate", "bill.tax.calculate", "prepaid.balance.check",
            "voucher.validate", "voucher.audit.report", "product.promo.validate",
            "product.eligibility.check", "product.compatibility.check",
            "sub.lifetime.value", "sub.churn.predict", "sales.commission.calc",
            "sales.target.track", "sales.funnel.analyze", "dealer.commission",
            "dealer.sales.report", "dealer.performance.track", "partner.revenue.share",
            "portal.profile.view", "portal.usage.view", "portal.invoice.view",
            "nps.score.calculate", "nps.feedback.analyze", "nps.report.monthly",
            "nps.benchmark.industry", "nps.trend.analyze",
        ]
        for skill in readonly:
            self.assertIn(skill, commercial.HANDLERS)
            self.assertNotIn(skill, commercial.MUTATIONS)

    def test_schemas_strict_and_bounded(self):
        def walk(node):
            if not isinstance(node, dict):
                return
            if node.get("type") == "object":
                self.assertIs(node.get("additionalProperties"), False)
                for required in node.get("required", []):
                    self.assertIn(required, node.get("properties", {}))
            if node.get("type") == "string":
                self.assertIn("maxLength", node)
                self.assertLessEqual(node["maxLength"], 4000)
            if node.get("type") == "array":
                self.assertIn("items", node)
                self.assertIn("maxItems", node)
                self.assertLessEqual(node["maxItems"], 10000)
            for key in ("minimum", "maximum"):
                if key in node:
                    self.assertIsInstance(node[key], int)
            for value in node.get("properties", {}).values():
                walk(value)
            if "items" in node:
                walk(node["items"])

        for skill, schema in commercial.SCHEMAS.items():
            self.assertEqual(schema.get("type"), "object", skill)
            walk(schema)
            json.dumps(schema)

    def test_schemas_have_no_forbidden_arguments(self):
        def names(node, acc):
            if not isinstance(node, dict):
                return acc
            if node.get("type") == "object":
                for name in node.get("properties", {}):
                    acc.add(name)
            for value in node.get("properties", {}).values():
                names(value, acc)
            if "items" in node:
                names(node["items"], acc)
            return acc

        for skill, schema in commercial.SCHEMAS.items():
            self.assertFalse(names(schema, set()) & FORBIDDEN_ARG_NAMES, skill)

    def test_ids_in_root_catalog(self):
        if not os.path.isfile(ROOT_CATALOG_PATH):
            self.skipTest("root catalog_source.json not reachable")
        with open(ROOT_CATALOG_PATH) as handle:
            catalog = json.load(handle)
        known = {row["id"] for row in catalog["skills"]}
        self.assertTrue(set(commercial.HANDLERS) <= known)

    def test_heuristic_handlers_have_limitations(self):
        for skill in ("sub.churn.predict", "sub.lifetime.value", "nps.feedback.analyze",
                      "sub.kyc.verify", "sub.address.verify", "bill.dunning.run",
                      "bill.refund.process", "prepaid.topup", "portal.package.change"):
            self.assertIn(skill, commercial.LIMITATIONS)


class BillingTest(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()

    def test_invoice_generate_and_duplicate_prevention(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        invoice = mk_invoice(ctx, subscriber["id"], product["id"])
        self.assertEqual(invoice["status"], "open")
        self.assertEqual(invoice["paid_idr"], 0)
        self.assertEqual(invoice["amount_idr"], 250000)
        expect_error(self, "bill.invoice.generate",
                     {"subscriber_id": subscriber["id"], "product_id": product["id"],
                      "period": "2026-10", "due_at": "2026-10-25T00:00:00Z"},
                     "duplicate_invoice")

    def test_cycle_run_creates_then_skips(self):
        ctx = self.ctx
        product = mk_product(ctx)
        sub_a = mk_sub(ctx, name="A")
        sub_b = mk_sub(ctx, name="B")
        for subscriber in (sub_a, sub_b):
            activate(ctx, subscriber["id"])
            attach_package(ctx, subscriber["id"], product["id"])
        mk_sub(ctx, name="C-no-package", package_id=None)
        result = run(ctx, "bill.cycle.run", {"period": "2026-10", "due_at": "2026-10-25T00:00:00Z"})
        self.assertEqual(result["invoices_created"], 2)
        again = run(ctx, "bill.cycle.run", {"period": "2026-10", "due_at": "2026-10-25T00:00:00Z"})
        self.assertEqual(again["invoices_created"], 0)
        self.assertEqual(again["skipped_existing"], 2)

    def test_payment_record_unverified_and_match_requires_verified(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        invoice = mk_invoice(ctx, subscriber["id"], product["id"])
        payment = run(ctx, "bill.payment.record", {
            "subscriber_id": subscriber["id"], "amount_idr": 250000,
            "provider_reference": "REF-001", "channel": "bank_transfer",
            "evidence": {"bank_ref": "BNK-9", "confirmed_at": "2026-10-24T09:00:00Z"},
        })["payment"]
        self.assertEqual(payment["status"], "recorded_unverified")
        expect_error(self, "bill.payment.match", {"payment_id": payment["id"]}, "payment_not_verified", 409)
        expect_error(self, "bill.payment.record",
                     {"subscriber_id": subscriber["id"], "amount_idr": 1,
                      "provider_reference": "REF-001", "channel": "cash"},
                     "duplicate_provider_reference")
        verified = mk_verified_payment(ctx, subscriber["id"], 250000, "VERIF-OK-1")
        result = run(ctx, "bill.payment.match", {"payment_id": verified["id"]})
        self.assertEqual(result["allocations"][0]["invoice_status"], "paid")
        stored = ctx.get("invoice", invoice["id"])
        self.assertEqual(stored["status"], "paid")
        self.assertEqual(stored["paid_idr"], 250000)

    def test_match_fifo_allocation(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        old = mk_invoice(ctx, subscriber["id"], product["id"], period="2026-08", due_at="2026-08-25T00:00:00Z")
        newer = mk_invoice(ctx, subscriber["id"], product["id"], period="2026-09", due_at="2026-09-25T00:00:00Z")
        payment = mk_verified_payment(ctx, subscriber["id"], 300000, "VERIF-FIFO")
        result = run(ctx, "bill.payment.match", {"payment_id": payment["id"]})
        allocs = {row["invoice_id"]: row for row in result["allocations"]}
        self.assertEqual(allocs[old["id"]]["invoice_status"], "paid")
        self.assertEqual(allocs[newer["id"]]["invoice_status"], "partially_paid")
        self.assertEqual(ctx.get("invoice", newer["id"])["paid_idr"], 50000)
        self.assertEqual(result["unmatched_idr"], 0)
        overpay = mk_verified_payment(ctx, subscriber["id"], 999999, "VERIF-OVER")
        result2 = run(ctx, "bill.payment.match", {"payment_id": overpay["id"]})
        self.assertEqual(result2["unmatched_idr"], 799999)
        self.assertEqual(ctx.get("invoice", newer["id"])["status"], "paid")

    def test_arrears_aging(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        mk_invoice(ctx, subscriber["id"], product["id"], period="2026-08", due_at="2026-08-25T00:00:00Z")
        mk_invoice(ctx, subscriber["id"], product["id"], period="2026-10", due_at="2026-11-25T00:00:00Z")
        report = run(ctx, "bill.arrears.calculate",
                     {"subscriber_id": subscriber["id"], "as_of": "2026-10-24T10:00:00Z"})
        self.assertEqual(report["total_arrears_idr"], 500000)
        self.assertEqual(report["aging_idr"]["90_plus"], 0)
        self.assertEqual(report["aging_idr"]["31_60"], 250000)
        self.assertEqual(report["aging_idr"]["not_due"], 250000)

    def test_dunning_requires_arrears_and_stays_local(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        expect_error(self, "bill.dunning.run",
                     {"subscriber_id": subscriber["id"], "level": "reminder"}, "no_arrears", 409)
        mk_invoice(ctx, subscriber["id"], product["id"])
        result = run(ctx, "bill.dunning.run",
                     {"subscriber_id": subscriber["id"], "level": "suspend_request"})
        self.assertEqual(result["dunning_step"]["level"], "suspend_request")
        self.assertEqual(ctx.get("subscriber", subscriber["id"])["status"], "pending")

    def test_late_fee_grace_and_cap(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        invoice = mk_invoice(ctx, subscriber["id"], product["id"], period="2026-09",
                             due_at="2026-09-25T00:00:00Z")
        within = run(ctx, "bill.late.fee",
                     {"invoice_id": invoice["id"], "rate_bps": 5000, "grace_days": 30,
                      "as_of": "2026-10-20T00:00:00Z"})
        self.assertEqual(within["late_fee_idr"], 0)
        late = run(ctx, "bill.late.fee",
                   {"invoice_id": invoice["id"], "rate_bps": 5000, "grace_days": 0,
                    "as_of": "2026-10-24T00:00:00Z"})
        self.assertEqual(late["days_late"], 29)
        expected = min(250000, (2 * 250000 * 29 * 5000 + 10000) // (2 * 10000))
        self.assertEqual(late["late_fee_idr"], expected)
        capped = run(ctx, "bill.late.fee",
                     {"invoice_id": invoice["id"], "rate_bps": 100000, "grace_days": 0,
                      "as_of": "2027-10-24T00:00:00Z"})
        self.assertEqual(capped["late_fee_idr"], 250000)
        self.assertTrue(capped["calculation_only"])

    def test_prorate_exact(self):
        result = run(self.ctx, "bill.prorate.calculate",
                     {"price_idr": 250000, "period": "2026-10", "active_from": "2026-10-16"})
        self.assertEqual(result["active_days"], 16)
        self.assertEqual(result["period_days"], 31)
        self.assertEqual(result["prorated_idr"], 129032)
        self.assertTrue(result["calculation_only"])
        april = run(self.ctx, "bill.prorate.calculate",
                    {"price_idr": 250000, "period": "2026-04", "active_from": "2026-04-21"})
        self.assertEqual(april["prorated_idr"], 83333)
        expect_error(self, "bill.prorate.calculate",
                     {"price_idr": 250000, "period": "2026-10", "active_from": "2026-11-01"},
                     "active_from_outside_period")

    def test_tax_explicit_rate_only(self):
        result = run(self.ctx, "bill.tax.calculate",
                     {"base_idr": 250000, "rate_bps": 1100, "policy_id": "ppn-policy-1",
                      "effective_date": "2026-10-01"})
        self.assertEqual(result["tax_idr"], 27500)
        self.assertEqual(result["gross_idr"], 277500)
        half_up = run(self.ctx, "bill.tax.calculate",
                      {"base_idr": 12345, "rate_bps": 1100, "policy_id": "ppn-policy-1",
                       "effective_date": "2026-10-01"})
        self.assertEqual(half_up["tax_idr"], 1358)
        other = run(self.ctx, "bill.tax.calculate",
                    {"base_idr": 250000, "rate_bps": 0, "policy_id": "zero-policy",
                     "effective_date": "2026-10-01"})
        self.assertEqual(other["tax_idr"], 0)
        self.assertTrue(result["calculation_only"])

    def test_adjustment_credit_limits_and_status(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        invoice = mk_invoice(ctx, subscriber["id"], product["id"])
        run(ctx, "bill.adjustment.create",
            {"subscriber_id": subscriber["id"], "invoice_id": invoice["id"], "direction": "credit",
             "amount_idr": 50000, "reason": "goodwill credit"})
        remaining = run(ctx, "bill.arrears.calculate", {"subscriber_id": subscriber["id"]})
        self.assertEqual(remaining["total_arrears_idr"], 200000)
        expect_error(self, "bill.adjustment.create",
                     {"subscriber_id": subscriber["id"], "invoice_id": invoice["id"], "direction": "credit",
                      "amount_idr": 999999, "reason": "too much"}, "adjustment_exceeds_balance")

    def test_discount_statement_deposit(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        invoice = mk_invoice(ctx, subscriber["id"], product["id"])
        result = run(ctx, "bill.discount.apply",
                     {"invoice_id": invoice["id"], "amount_idr": 10000, "reason": "promo welcome"})
        self.assertEqual(result["invoice_remaining_idr"], 240000)
        deposit1 = run(ctx, "bill.deposit.manage",
                       {"subscriber_id": subscriber["id"], "action": "collect", "amount_idr": 100000,
                        "reason": "install deposit"})
        self.assertEqual(deposit1["held_idr"], 100000)
        deposit2 = run(ctx, "bill.deposit.manage",
                       {"subscriber_id": subscriber["id"], "action": "release", "amount_idr": 30000,
                        "reason": "partial release"})
        self.assertEqual(deposit2["held_idr"], 70000)
        expect_error(self, "bill.deposit.manage",
                     {"subscriber_id": subscriber["id"], "action": "release", "amount_idr": 80000,
                      "reason": "too much"}, "insufficient_deposit", 409)
        statement = run(ctx, "bill.statement.generate",
                        {"subscriber_id": subscriber["id"], "period": "2026-10"})
        self.assertEqual(statement["billed_idr"], 250000)
        self.assertEqual(statement["remaining_idr"], 240000)


class PrepaidVoucherTest(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()

    def test_prepaid_topup_requires_adapter_proof(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        expect_error(self, "prepaid.topup",
                     {"subscriber_id": subscriber["id"], "amount_idr": 50000,
                      "funding": {"channel": "bank_transfer", "reference": "BX-1",
                                  "confirmed_at": "2026-10-24T09:00:00Z",
                                  "adapter_confirmation_id": "ADP-1",
                                  "verified_by_adapter": False}},
                     "funding_not_verified", 409)
        result = run(ctx, "prepaid.topup",
                     {"subscriber_id": subscriber["id"], "amount_idr": 50000,
                      "funding": {"channel": "bank_transfer", "reference": "BX-1",
                                  "confirmed_at": "2026-10-24T09:00:00Z",
                                  "adapter_confirmation_id": "ADP-1",
                                  "verified_by_adapter": True}})
        self.assertEqual(result["balance_idr"], 50000)
        run(ctx, "prepaid.deduct",
            {"subscriber_id": subscriber["id"], "amount_idr": 20000, "reason": "usage"})
        balance = run(ctx, "prepaid.balance.check", {"subscriber_id": subscriber["id"]})
        self.assertEqual(balance["balance_idr"], 30000)
        expect_error(self, "prepaid.deduct",
                     {"subscriber_id": subscriber["id"], "amount_idr": 999999, "reason": "usage"},
                     "insufficient_balance", 409)
        versions = {row["version"] for row in ctx.list("prepaid_ledger", limit=1000)}
        self.assertEqual(versions, {1})

    def test_voucher_hashed_and_validate(self):
        ctx = self.ctx
        batch = run(ctx, "voucher.generate", {"count": 2, "amount_idr": 25000,
                                              "expires_at": "2026-12-31T23:59:59Z"})
        self.assertEqual(len(batch["codes"]), 2)
        stored = ctx.list("voucher", limit=1000)
        self.assertEqual(len(stored), 2)
        code_hashes = {hashlib.sha256(code.encode()).hexdigest() for code in batch["codes"]}
        for voucher in stored:
            self.assertIn(voucher["code_hash"], code_hashes)
            for code in batch["codes"]:
                self.assertNotIn(code, json.dumps(voucher))
        check = run(ctx, "voucher.validate", {"code": batch["codes"][0]})
        self.assertEqual(check["status"], "active")
        self.assertEqual(check["amount_idr"], 25000)
        missing = run(ctx, "voucher.validate", {"code": "HRM-NOPE012345"})
        self.assertEqual(missing["status"], "not_found")

    def test_voucher_redeem_exactly_once_race(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        batch = run(ctx, "voucher.generate", {"count": 1, "amount_idr": 40000})
        code = batch["codes"][0]
        voucher = ctx.list("voucher", limit=1000)[0]
        with self.assertRaises(DomainError) as stale:
            ctx.update("voucher", voucher["id"], {"status": "active"},
                       expected_version=voucher["version"] - 1)
        self.assertEqual(stale.exception.code, "version_conflict")
        result = run(ctx, "voucher.redeem", {"code": code, "subscriber_id": subscriber["id"]})
        self.assertEqual(result["balance_idr"], 40000)
        expect_error(self, "voucher.redeem", {"code": code, "subscriber_id": subscriber["id"]},
                     "voucher_already_redeemed", 409)
        check = run(ctx, "voucher.validate", {"code": code})
        self.assertEqual(check["status"], "redeemed")

    def test_voucher_expire_audit_distribute(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        dealer = run(ctx, "dealer.register", {"name": "Outlet A", "region": "jakarta"})["dealer"]
        old = run(ctx, "voucher.generate", {"count": 2, "amount_idr": 10000,
                                            "expires_at": "2026-01-01T00:00:00Z", "batch_name": "old"})
        live = run(ctx, "voucher.generate", {"count": 2, "amount_idr": 10000,
                                             "expires_at": "2026-12-31T00:00:00Z", "batch_name": "live"})
        expired = run(ctx, "voucher.expire.handle", {})
        self.assertEqual(expired["expired_count"], 2)
        audit = run(ctx, "voucher.audit.report", {"batch_id": old["batch"]["id"]})
        self.assertEqual(audit["totals"]["expired"], 2)
        dist = run(ctx, "voucher.distribute",
                   {"batch_id": live["batch"]["id"], "dealer_id": dealer["id"], "max_count": 1})
        self.assertEqual(dist["distributed"], 1)
        report = run(ctx, "voucher.audit.report", {"batch_id": live["batch"]["id"]})
        self.assertEqual(report["totals"]["active"], 2)
        run(ctx, "voucher.redeem", {"code": live["codes"][0], "subscriber_id": subscriber["id"]})
        run(ctx, "voucher.redeem", {"code": live["codes"][1], "subscriber_id": subscriber["id"]})
        suspicious = run(ctx, "voucher.audit.report",
                         {"batch_id": live["batch"]["id"], "suspicious_threshold": 2})
        self.assertEqual(len(suspicious["suspicious_redeemers"]), 1)
        self.assertEqual(suspicious["suspicious_redeemers"][0]["count"], 2)


class ProductTest(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()

    def test_pricing_versioned_history(self):
        ctx = self.ctx
        product = mk_product(ctx, name="Fiber 50", price=350000)
        result = run(ctx, "product.pricing.set",
                     {"product_id": product["id"], "price_idr": 400000, "reason": "cost increase"})
        self.assertEqual(result["old_price_idr"], 350000)
        self.assertEqual(result["new_price_idr"], 400000)
        history = ctx.list("pricing_history", {"product_id": product["id"]}, limit=1000)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["old_price_idr"], 350000)
        self.assertEqual(ctx.get("product", product["id"])["price_idr"], 400000)
        expect_error(self, "product.create", {"name": "Fiber 50", "price_idr": 1}, "duplicate_product_name")

    def test_lifecycle_and_eligibility(self):
        ctx = self.ctx
        product = mk_product(ctx, name="Biz 100", segment_target="business")
        subscriber = mk_sub(ctx, segment="residential")
        check = run(ctx, "product.eligibility.check",
                    {"subscriber_id": subscriber["id"], "product_id": product["id"]})
        self.assertFalse(check["eligible"])
        retired = run(ctx, "product.lifecycle.manage", {"product_id": product["id"], "action": "retire"})
        self.assertFalse(retired["product"]["active"])
        expect_error(self, "product.lifecycle.manage",
                     {"product_id": product["id"], "action": "retire"}, "invalid_lifecycle_transition", 409)
        expect_error(self, "product.update",
                     {"product_id": product["id"], "description": "x"}, "product_inactive")
        react = run(ctx, "product.lifecycle.manage", {"product_id": product["id"], "action": "reactivate"})
        self.assertTrue(react["product"]["active"])
        run(ctx, "sub.segmentation", {"subscriber_id": subscriber["id"], "segment": "business"})
        check2 = run(ctx, "product.eligibility.check",
                     {"subscriber_id": subscriber["id"], "product_id": product["id"]})
        self.assertTrue(check2["eligible"])

    def test_bundling_and_compatibility(self):
        ctx = self.ctx
        net = mk_product(ctx, name="Net 20")
        tv = mk_product(ctx, name="TV Basic")
        bundle = run(ctx, "product.bundling",
                     {"name": "Duo", "component_product_ids": [net["id"], tv["id"]], "price_idr": 300000})
        self.assertTrue(bundle["product"]["is_bundle"])
        expect_error(self, "product.bundling",
                     {"name": "Tri", "component_product_ids": [bundle["product"]["id"], tv["id"]],
                      "price_idr": 1}, "component_is_bundle")
        compat = run(ctx, "product.compatibility.check",
                     {"product_id_a": net["id"], "product_id_b": tv["id"]})
        self.assertTrue(compat["compatible"])
        nested = run(ctx, "product.compatibility.check",
                     {"product_id_a": bundle["product"]["id"], "product_id_b": net["id"]})
        self.assertFalse(nested["compatible"])
        feature = run(ctx, "product.feature.attaching",
                      {"product_id": net["id"], "feature_name": "static_ip", "price_idr": 50000})
        self.assertEqual(feature["product_feature"]["price_idr"], 50000)
        expect_error(self, "product.feature.attaching",
                     {"product_id": net["id"], "feature_name": "static_ip", "price_idr": 1},
                     "duplicate_feature")


class SubscriberTest(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()

    def test_status_transitions_and_activation_guard(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        expect_error(self, "sub.status.manage",
                     {"subscriber_id": subscriber["id"], "new_status": "active"},
                     "activation_confirmation_required")
        activate(ctx, subscriber["id"])
        self.assertEqual(ctx.get("subscriber", subscriber["id"])["status"], "active")
        run(ctx, "sub.status.manage", {"subscriber_id": subscriber["id"], "new_status": "suspended"})
        run(ctx, "sub.status.manage", {"subscriber_id": subscriber["id"], "new_status": "active",
                                       "local_activation_confirmed": True})
        run(ctx, "sub.status.manage", {"subscriber_id": subscriber["id"], "new_status": "terminated"})
        expect_error(self, "sub.status.manage",
                     {"subscriber_id": subscriber["id"], "new_status": "active",
                      "local_activation_confirmed": True}, "invalid_status_transition", 409)
        expect_error(self, "sub.profile.update",
                     {"subscriber_id": subscriber["id"], "name": "Renamed"}, "subscriber_terminated", 409)

    def test_kyc_never_verified(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        result = run(ctx, "sub.kyc.verify",
                     {"subscriber_id": subscriber["id"],
                      "evidence": {"doc_type": "ktp", "doc_ref": "KTP-REF-1"}})
        self.assertFalse(result["verified"])
        self.assertEqual(result["kyc_case"]["status"], "pending_manual_verification")
        self.assertIsNone(result["kyc_case"]["reviewed_by"])

    def test_contacts_single_primary(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        first = run(ctx, "sub.contact.manage",
                    {"subscriber_id": subscriber["id"], "action": "add", "name": "A",
                     "is_primary": True})["contact"]
        second = run(ctx, "sub.contact.manage",
                     {"subscriber_id": subscriber["id"], "action": "add", "name": "B",
                      "is_primary": True})["contact"]
        self.assertFalse(ctx.get("subscriber_contact", first["id"])["is_primary"])
        self.assertTrue(ctx.get("subscriber_contact", second["id"])["is_primary"])
        run(ctx, "sub.contact.manage",
            {"subscriber_id": subscriber["id"], "action": "set_primary", "contact_id": first["id"]})
        self.assertTrue(ctx.get("subscriber_contact", first["id"])["is_primary"])
        removed = run(ctx, "sub.contact.manage",
                      {"subscriber_id": subscriber["id"], "action": "remove", "contact_id": second["id"]})
        self.assertEqual(removed["contact"]["status"], "removed")
        expect_error(self, "sub.contact.manage",
                     {"subscriber_id": subscriber["id"], "action": "update", "contact_id": second["id"],
                      "name": "B2"}, "contact_removed", 409)

    def test_contract_lifecycle(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        contract = run(ctx, "sub.contract.manage",
                       {"subscriber_id": subscriber["id"], "action": "create", "months": 12,
                        "start_at": "2026-10-01", "penalty_idr": 500000})["contract"]
        self.assertEqual(contract["start_at"], "2026-10-01")
        self.assertEqual(contract["end_at"], "2027-09-30")
        expect_error(self, "sub.contract.manage",
                     {"subscriber_id": subscriber["id"], "action": "create", "months": 6},
                     "contract_already_active", 409)
        ended = run(ctx, "sub.contract.manage",
                    {"subscriber_id": subscriber["id"], "action": "end", "contract_id": contract["id"]})
        self.assertEqual(ended["contract"]["status"], "ended")

    def test_document_store_metadata_only(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        result = run(ctx, "sub.document.store",
                     {"subscriber_id": subscriber["id"], "doc_type": "ktp", "doc_ref": "DOC-REF-9",
                      "content_sha256": "a" * 64})
        self.assertEqual(result["document"]["status"], "received_unverified")

    def test_merge_duplicate_moves_references(self):
        ctx = self.ctx
        product = mk_product(ctx)
        survivor = mk_sub(ctx, name="Keep")
        duplicate = mk_sub(ctx, name="Dup", external_customer_id="EXT-DUP")
        invoice = mk_invoice(ctx, duplicate["id"], product["id"])
        run(ctx, "prepaid.topup",
            {"subscriber_id": duplicate["id"], "amount_idr": 10000,
             "funding": {"channel": "cash", "reference": "CASH-1",
                         "confirmed_at": "2026-10-23T00:00:00Z",
                         "adapter_confirmation_id": "ADP-9", "verified_by_adapter": True}})
        result = run(ctx, "sub.merge.duplicate",
                     {"survivor_id": survivor["id"], "duplicate_id": duplicate["id"], "reason": "double signup"})
        self.assertEqual(result["moved_counts"]["invoice"], 1)
        self.assertEqual(ctx.get("invoice", invoice["id"])["subscriber_id"], survivor["id"])
        merged = ctx.get("subscriber", duplicate["id"])
        self.assertEqual(merged["status"], "terminated")
        self.assertEqual(merged["merged_into"], survivor["id"])
        expect_error(self, "sub.merge.duplicate",
                     {"survivor_id": survivor["id"], "duplicate_id": duplicate["id"], "reason": "again"},
                     "duplicate_already_merged", 409)
        expect_error(self, "sub.create",
                     {"name": "Dup2", "external_customer_id": "EXT-DUP"},
                     "duplicate_external_customer")

    def test_churn_and_clv_heuristics(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx, created_ok=True)
        invoice = mk_invoice(ctx, subscriber["id"], product["id"])
        run(ctx, "bill.payment.record", {"subscriber_id": subscriber["id"], "amount_idr": 250000,
                                         "provider_reference": "R-1", "channel": "cash"})
        report = run(ctx, "sub.churn.predict", {"subscriber_id": subscriber["id"]})
        self.assertTrue(0 <= report["risk_score"] <= 100)
        self.assertIn(report["category"], ("low", "medium", "high"))
        self.assertTrue(report["signals"]["has_arrears"])
        clv = run(ctx, "sub.lifetime.value", {"subscriber_id": subscriber["id"], "expected_months": 12})
        self.assertEqual(clv["paid_to_date_idr"], 0)
        self.assertEqual(clv["projected_clv_idr"], 0)
        self.assertTrue(clv["heuristic"])
        run(ctx, "bill.payment.match", {"payment_id": mk_verified_payment(ctx, subscriber["id"], 250000)["id"]})
        clv2 = run(ctx, "sub.lifetime.value", {"subscriber_id": subscriber["id"], "expected_months": 12})
        self.assertEqual(clv2["paid_to_date_idr"], 250000)
        self.assertEqual(clv2["projected_clv_idr"], 250000 * 12)
        self.assertEqual(ctx.get("invoice", invoice["id"])["status"], "paid")


class SalesDealerTest(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()

    def test_order_approval_commission_target(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        order = run(ctx, "sales.order.create",
                    {"subscriber_id": subscriber["id"], "product_id": product["id"],
                     "sales_id": "sales-andi"})["order"]
        self.assertEqual(order["status"], "draft")
        approved = run(ctx, "sales.order.approve", {"order_id": order["id"]})["order"]
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["approved_by"], ctx.actor)
        expect_error(self, "sales.order.approve", {"order_id": order["id"]},
                     "invalid_order_transition", 409)
        commission = run(ctx, "sales.commission.calc",
                         {"period": "2026-10", "earner_type": "sales", "earner_id": "sales-andi",
                          "rate_bps": 500})
        self.assertEqual(commission["base_idr"], 0)  # Approved order alone is not paid.
        self.assertEqual(commission["commission_idr"], 0)
        invoice=mk_invoice(ctx,subscriber['id'],product['id'])
        verified=mk_verified_payment(ctx,subscriber['id'],250000)
        run(ctx,'bill.payment.match',{'payment_id':verified['id']})
        commission=run(ctx,'sales.commission.calc',{'period':'2026-10','earner_type':'sales','earner_id':'sales-andi','rate_bps':500})
        self.assertEqual(commission['base_idr'],250000);self.assertEqual(commission['commission_idr'],12500)
        target = run(ctx, "sales.target.track",
                     {"sales_id": "sales-andi", "period": "2026-10", "target_idr": 500000})
        self.assertEqual(target["attainment_bps"], 5000)
        self.assertEqual(target["gap_idr"], 250000)

    def test_quote_discount_order_link(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        quote = run(ctx, "sales.quote.generate",
                    {"subscriber_id": subscriber["id"], "product_id": product["id"],
                     "install_fee_idr": 300000, "discount_idr": 50000})["quote"]
        self.assertEqual(quote["total_idr"], 500000)
        order = run(ctx, "sales.order.create",
                    {"subscriber_id": subscriber["id"], "product_id": product["id"],
                     "quote_id": quote["id"]})["order"]
        self.assertEqual(order["discount_idr"], 50000)
        other = mk_sub(ctx, name="Other")
        expect_error(self, "sales.order.create",
                     {"subscriber_id": other["id"], "product_id": product["id"], "quote_id": quote["id"]},
                     "quote_subscriber_mismatch")

    def test_lead_funnel_campaign(self):
        ctx = self.ctx
        campaign = run(ctx, "sales.campaign.track",
                       {"action": "create", "name": "Launch", "channel": "social",
                        "starts_at": "2026-10-01", "ends_at": "2026-10-31", "budget_idr": 1000000})["campaign"]
        lead = run(ctx, "sales.lead.capture",
                   {"source": "web", "contact_name": "Lead One", "contact_ref": "lead-1-ref",
                    "campaign_id": campaign["id"]})["lead"]
        qualified = run(ctx, "sales.lead.qualify",
                        {"lead_id": lead["id"], "decision": "qualified", "reason": "budget confirmed",
                         "coverage_evidence_ref": "COV-1"})["lead"]
        self.assertEqual(qualified["status"], "qualified")
        expect_error(self, "sales.lead.qualify",
                     {"lead_id": lead["id"], "decision": "qualified", "reason": "again"},
                     "lead_already_processed", 409)
        funnel = run(ctx, "sales.funnel.analyze", {})
        self.assertEqual(funnel["leads"]["by_status"]["qualified"], 1)
        report = run(ctx, "sales.campaign.track", {"action": "report", "campaign_id": campaign["id"]})
        self.assertEqual(report["leads"], 1)

    def test_referral_flow(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        referral = run(ctx, "sales.referral.manage",
                       {"action": "record", "referrer_subscriber_id": subscriber["id"],
                        "referred_contact_ref": "friend-ref-1"})["referral"]
        self.assertEqual(referral["status"], "recorded")
        approved = run(ctx, "sales.referral.manage",
                       {"action": "approve_reward", "referral_id": referral["id"],
                        "reward_idr": 50000})["referral"]
        self.assertEqual(approved["status"], "reward_approved")
        self.assertEqual(approved["reward_idr"], 50000)

    def test_dealer_commission_settlement_report_termination(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        dealer = run(ctx, "dealer.register",
                     {"name": "Dealer Satu", "region": "bandung", "commission_bps": 300})["dealer"]
        order = run(ctx, "sales.order.create",
                    {"subscriber_id": subscriber["id"], "product_id": product["id"],
                     "dealer_id": dealer["id"]})["order"]
        run(ctx, "sales.order.approve", {"order_id": order["id"]})
        commission = run(ctx, "dealer.commission", {"dealer_id": dealer["id"], "period": "2026-10"})
        self.assertEqual(commission["commission_idr"], 0)
        mk_invoice(ctx,subscriber['id'],product['id'])
        verified=mk_verified_payment(ctx,subscriber['id'],250000)
        run(ctx,'bill.payment.match',{'payment_id':verified['id']})
        commission=run(ctx,'dealer.commission',{'dealer_id':dealer['id'],'period':'2026-10'})
        self.assertEqual(commission['commission_idr'],7500)
        settlement = run(ctx, "dealer.settlement", {"dealer_id": dealer["id"], "period": "2026-10"})
        self.assertEqual(settlement["settlement"]["status"], "recorded_unpaid")
        expect_error(self, "dealer.settlement",
                     {"dealer_id": dealer["id"], "period": "2026-10"}, "settlement_already_recorded")
        report = run(ctx, "dealer.sales.report", {"dealer_id": dealer["id"], "period": "2026-10"})
        self.assertEqual(report["sales_idr"], 250000)
        ranking = run(ctx, "dealer.performance.track", {"period": "2026-10"})
        self.assertEqual(ranking["ranking"][0]["dealer_id"], dealer["id"])
        terminated = run(ctx, "dealer.termination",
                         {"dealer_id": dealer["id"], "reason": "contract breach"})
        self.assertEqual(terminated["dealer"]["status"], "terminated")
        self.assertEqual(terminated["open_settlements_idr"], 7500)
        expect_error(self, "dealer.termination",
                     {"dealer_id": dealer["id"], "reason": "again"}, "invalid_dealer_transition", 409)

    def test_dealer_stock_allocation_vouchers(self):
        ctx = self.ctx
        dealer = run(ctx, "dealer.register", {"name": "Dealer Dua", "region": "medan"})["dealer"]
        batch = run(ctx, "voucher.generate", {"count": 3, "amount_idr": 10000})
        result = run(ctx, "dealer.stock.allocation",
                     {"dealer_id": dealer["id"], "item": "voucher_batch",
                      "voucher_batch_id": batch["batch"]["id"], "qty": 2})
        self.assertEqual(result["vouchers_assigned"], 2)
        assigned = [v for v in ctx.list("voucher", limit=1000) if v.get("dealer_id") == dealer["id"]]
        self.assertEqual(len(assigned), 2)

    def test_partner_share_exact_and_portal_access(self):
        ctx = self.ctx
        contract = run(ctx, "partner.contract.manage",
                       {"action": "create", "partner_name": "Reseller Jaya",
                        "revenue_share_bps": 1500, "starts_at": "2026-01-01",
                        "ends_at": "2026-12-31"})["partner_contract"]
        share = run(ctx, "partner.revenue.share",
                    {"contract_id": contract["id"], "revenue_idr": 123457})
        self.assertEqual(share["partner_share_idr"], 18519)
        self.assertEqual(share["company_retained_idr"], 104938)
        self.assertEqual(share["partner_share_idr"] + share["company_retained_idr"], 123457)
        access = run(ctx, "partner.portal.access",
                     {"contract_id": contract["id"], "scope": ["orders", "statements"]})
        self.assertFalse(access["partner_access"]["credentials_issued"])


class PortalNpsTest(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()

    def test_portal_views_ticket_package_notifications(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        invoice = mk_invoice(ctx, subscriber["id"], product["id"])
        profile = run(ctx, "portal.profile.view", {"subscriber_id": subscriber["id"]})
        self.assertEqual(profile["profile"]["status"], "pending")
        view = run(ctx, "portal.invoice.view", {"subscriber_id": subscriber["id"]})
        self.assertEqual(view["open_total_idr"], 250000)
        usage = run(ctx, "portal.usage.view", {"subscriber_id": subscriber["id"]})
        self.assertEqual(usage["count"], 0)
        self.assertIn("fabricated", usage["note"])
        ticket = run(ctx, "portal.ticket.create",
                     {"subscriber_id": subscriber["id"], "subject": "Internet lambat sejak pagi",
                      "severity": "P2"})["ticket"]
        self.assertEqual(ticket["status"], "open")
        self.assertEqual(ticket["severity"], "P2")
        new_product = mk_product(ctx, name="Home 50Mbps", price=400000)
        change = run(ctx, "portal.package.change",
                     {"subscriber_id": subscriber["id"], "new_product_id": new_product["id"],
                      "effective_period": "2026-11"})
        self.assertEqual(change["package_change"]["status"], "recorded_pending_provisioning")
        self.assertEqual(ctx.get("subscriber", subscriber["id"])["package_id"], new_product["id"])
        expect_error(self, "portal.package.change",
                     {"subscriber_id": subscriber["id"], "new_product_id": new_product["id"],
                      "effective_period": "2026-11"}, "package_unchanged", 409)
        feedback = run(ctx, "portal.feedback.submit",
                       {"subscriber_id": subscriber["id"], "category": "service", "rating": 4,
                        "text": "Pemasangan cepat dan teknisi ramah"})
        self.assertEqual(feedback["feedback"]["status"], "received")
        notification = run(ctx, "portal.notification.center",
                           {"action": "queue", "subscriber_id": subscriber["id"],
                            "template": "invoice_ready", "payload_ref": invoice["id"]})
        self.assertEqual(notification["notification"]["status"], "recorded_not_sent")
        listed = run(ctx, "portal.notification.center",
                     {"action": "list", "subscriber_id": subscriber["id"]})
        self.assertEqual(listed["count"], 1)

    def test_nps_score_known_values(self):
        result = run(self.ctx, "nps.score.calculate",
                     {"responses": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]})
        self.assertEqual(result["promoters"], 2)
        self.assertEqual(result["passives"], 2)
        self.assertEqual(result["detractors"], 6)
        self.assertEqual(result["nps_score"], -40)
        self.assertEqual(result["nps_bps"], -4000)
        positive = run(self.ctx, "nps.score.calculate", {"responses": [9, 9, 9, 1]})
        self.assertEqual(positive["nps_score"], 50)
        self.assertTrue(-100 <= positive["nps_score"] <= 100)

    def test_nps_trend(self):
        result = run(self.ctx, "nps.trend.analyze",
                     {"series": [
                         {"period": "2026-08", "responses": [9, 9, 9, 9]},
                         {"period": "2026-09", "responses": [1, 1, 1, 1]},
                         {"period": "2026-10", "responses": [10, 9, 8, 7]},
                     ]})
        self.assertEqual([row["nps_score"] for row in result["trend"]], [100, -100, 50])
        self.assertEqual(result["trend"][1]["delta"], -200)
        self.assertEqual(result["overall_direction"], "improving")
        expect_error(self, "nps.trend.analyze",
                     {"series": [
                         {"period": "2026-10", "responses": [9]},
                         {"period": "2026-09", "responses": [9]},
                     ]}, "periods_not_ascending")

    def test_nps_report_monthly(self):
        ctx = self.ctx
        period = "2026-09"
        empty = run(ctx, "nps.report.monthly", {"period": period})
        self.assertIsNone(empty["nps_score"])
        ctx.create("nps_response", {"period": period, "score": 10, "subscriber_id": None})
        ctx.create("nps_response", {"period": period, "score": 3, "subscriber_id": None})
        report = run(ctx, "nps.report.monthly", {"period": period})
        self.assertEqual(report["source"], "nps_response_records")
        self.assertEqual(report["nps_score"], 0)
        explicit = run(ctx, "nps.report.monthly", {"period": period, "responses": [9, 9, 2]})
        self.assertEqual(explicit["nps_score"], 33)  # Round final net ratio, not separate percentages.

    def test_nps_feedback_benchmark_followup_plan(self):
        ctx = self.ctx
        good = run(ctx, "nps.feedback.analyze", {"text": "pemasangan cepat dan bagus"})
        self.assertEqual(good["sentiment"], "positive")
        bad = run(ctx, "nps.feedback.analyze", {"text": "internet lambat, sering putus"})
        self.assertEqual(bad["sentiment"], "negative")
        benchmark = run(ctx, "nps.benchmark.industry",
                        {"our_nps": 55, "industry_benchmark": 40, "sector": "isp-residential"})
        self.assertEqual(benchmark["verdict"], "leading")
        self.assertEqual(benchmark["gap"], 15)
        subscriber = mk_sub(ctx)
        task = run(ctx, "nps.detractor.followup",
                   {"subscriber_id": subscriber["id"], "reason": "detractor after install",
                    "priority": "high"})
        self.assertEqual(task["followup_task"]["status"], "open")
        plan = run(ctx, "nps.action.plan",
                   {"area": "support", "actions": ["add staff", "faster escalation"],
                    "period": "2026-10"})
        self.assertEqual(plan["nps_action_plan"]["status"], "draft")


class RobustnessTest(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()

    def test_money_rejects_float_and_bool(self):
        with self.assertRaises(DomainError) as caught:
            run(self.ctx, "bill.tax.calculate",
                {"base_idr": 100.5, "rate_bps": 1100, "policy_id": "p", "effective_date": "2026-10-01"})
        self.assertEqual(caught.exception.code, "invalid_amount_idr")
        with self.assertRaises(DomainError):
            run(self.ctx, "bill.tax.calculate",
                {"base_idr": True, "rate_bps": 1100, "policy_id": "p", "effective_date": "2026-10-01"})

    def test_events_never_leak_voucher_codes(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        batch = run(ctx, "voucher.generate", {"count": 2, "amount_idr": 5000})
        run(ctx, "voucher.redeem", {"code": batch["codes"][0], "subscriber_id": subscriber["id"]})
        blob = json.dumps(ctx.events)
        for code in batch["codes"]:
            self.assertNotIn(code, blob)

    def test_handler_outputs_are_jsonable(self):
        ctx = self.ctx
        product = mk_product(ctx)
        subscriber = mk_sub(ctx)
        mk_invoice(ctx, subscriber["id"], product["id"])
        samples = [
            ("bill.invoice.generate", {"subscriber_id": subscriber["id"], "product_id": product["id"],
                                       "period": "2026-09", "due_at": "2026-09-30T00:00:00Z"}),
            ("bill.arrears.calculate", {"subscriber_id": subscriber["id"]}),
            ("portal.invoice.view", {"subscriber_id": subscriber["id"]}),
            ("nps.score.calculate", {"responses": [9, 5, 1]}),
        ]
        for skill, args in samples:
            json.dumps(run(ctx, skill, args))

    def test_usage_view_returns_records_without_fabrication(self):
        ctx = self.ctx
        subscriber = mk_sub(ctx)
        ctx.create("usage_record", {"subscriber_id": subscriber["id"], "period": "2026-10",
                                    "bytes_in": 1000, "source": "operations-evidence"})
        usage = run(ctx, "portal.usage.view", {"subscriber_id": subscriber["id"]})
        self.assertEqual(usage["count"], 1)
        self.assertEqual(usage["usage_records"][0]["source"], "operations-evidence")


if __name__ == "__main__":
    print("common loaded from: %s" % COMMON_ORIGIN["value"])
    unittest.main(verbosity=2)

"""Harmonika Telco commercial domain engine (engines 5,6,7,8,9,10,19,25).

Deterministic local BSS domain operations over the root-provided ctx
(SQLite tenant transaction). All money is whole-rupiah integers; no floats.
No network, messaging, payment, KYC, or network-device actions are performed:
external effects require verified adapters owned by root. IDs implemented here
match the canonical catalog exactly; unimplemented IDs are listed in
LIMITATIONS with their exact missing dependency.
"""
import datetime as _dt
import hashlib as _hashlib
import re as _re
import secrets as _secrets

from .common import DomainError, require, identifier, money, now_iso, parse_time

MAX_MONEY = 10 ** 15
LIST_CAP = 1000

_PERIOD_RE = _re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_DATE_RE = _re.compile(r"\d{4}-\d{2}-\d{2}")
_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

SEGMENTS = ("residential", "business", "enterprise", "wholesale")
SUB_STATUSES = ("pending", "active", "suspended", "terminated")
_INVOICE_OPEN = ("open", "partially_paid")


def _schema(properties, required):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(required),
        "properties": properties,
    }


def _sid():
    return {"type": "string", "minLength": 1, "maxLength": 100, "pattern": r"[A-Za-z0-9_-]{1,100}"}


def _text(lo=1, hi=200):
    return {"type": "string", "minLength": lo, "maxLength": hi}


def _ref(hi=100):
    return {"type": "string", "minLength": 1, "maxLength": hi, "pattern": r"[A-Za-z0-9][A-Za-z0-9._@:+-]{0,99}"}


def _phone():
    return {"type": "string", "minLength": 5, "maxLength": 32, "pattern": r"\+?[0-9][0-9 -]{4,31}"}


def _sha256():
    return {"type": "string", "minLength": 64, "maxLength": 64, "pattern": r"[0-9a-f]{64}"}


def _money():
    return {"type": "integer", "minimum": 0, "maximum": MAX_MONEY}


def _money_pos():
    return {"type": "integer", "minimum": 1, "maximum": MAX_MONEY}


def _int(lo, hi):
    return {"type": "integer", "minimum": lo, "maximum": hi}


def _enum(values):
    return {"enum": list(values)}


_TS = {"type": "string", "minLength": 10, "maxLength": 40}
_PERIOD = {"type": "string", "minLength": 7, "maxLength": 7, "pattern": r"\d{4}-(0[1-9]|1[0-2])"}
_DATE = {"type": "string", "minLength": 10, "maxLength": 10, "pattern": r"\d{4}-\d{2}-\d{2}"}


def _arr(items, lo=1, hi=100):
    return {"type": "array", "items": items, "minItems": lo, "maxItems": hi}


def _period(value, code="invalid_period"):
    require(type(value) is str and _PERIOD_RE.fullmatch(value), code)
    year, month = int(value[:4]), int(value[5:7])
    require(1 <= month <= 12 and 1900 <= year <= 2999, code)
    return year, month


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _period_days(value):
    year, month = _period(value)
    if month == 2 and _is_leap(year):
        return 29
    return _MONTH_DAYS[month - 1]


def _period_bounds(value):
    year, month = _period(value)
    first = _dt.date(year, month, 1)
    last = _dt.date(year, month, _period_days(value))
    return first, last


def _date(value, code="invalid_date"):
    require(type(value) is str and _DATE_RE.fullmatch(value), code)
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise DomainError(code) from None


def _ts(value, code="invalid_timestamp"):
    return parse_time(value)


def _now(ctx):
    return _ts(ctx.now, "invalid_ctx_now")


def _div_half_up(numerator, denominator):
    require(type(numerator) is int and numerator >= 0, "invalid_numerator")
    require(type(denominator) is int and denominator > 0, "invalid_denominator")
    return (2 * numerator + denominator) // (2 * denominator)


def _apply_bps(amount, bps):
    return _div_half_up(amount * bps, 10000)


def _rid(value):
    return identifier(value)


def _need(ctx, kind, rec_id, code="not_found"):
    require(type(rec_id) is str, code)
    return ctx.get(kind, identifier(rec_id))


def _bounded(ctx, kind, where=None):
    return ctx.list(kind, where=where, limit=LIST_CAP)


def _unique(ctx, kind, where, code):
    require(not ctx.list(kind, where=where, limit=1), code)


def _pick(args, name, default=None):
    value = args.get(name, default)
    return value


def _invoice_adjustments(ctx, invoice_id):
    credits = 0
    debits = 0
    for adj in _bounded(ctx, "adjustment", {"invoice_id": invoice_id}):
        if adj.get("direction") == "credit":
            credits += adj["amount_idr"]
        elif adj.get("direction") == "debit":
            debits += adj["amount_idr"]
    return credits, debits


def _invoice_state(ctx, invoice):
    credits, debits = _invoice_adjustments(ctx, invoice["id"])
    remaining = max(0, invoice["amount_idr"] - invoice["paid_idr"] - credits + debits)
    if remaining <= 0:
        status = "paid"
    elif invoice["paid_idr"] > 0:
        status = "partially_paid"
    else:
        status = "open"
    return remaining, status


def _sync_invoice_status(ctx, invoice):
    remaining, status = _invoice_state(ctx, invoice)
    if status != invoice["status"] and invoice["status"] != "void":
        invoice = ctx.update("invoice", invoice["id"], {"status": status})
    return invoice, remaining


def _subscriber(ctx, args):
    return _need(ctx, "subscriber", args["subscriber_id"], "subscriber_not_found")


def _product(ctx, args):
    return _need(ctx, "product", args["product_id"], "product_not_found")


def _require_live_product(product):
    require(product.get("active") is True and product.get("lifecycle_status") != "retired", "product_inactive")


def _require_billable_subscriber(subscriber):
    require(subscriber["status"] != "terminated", "subscriber_terminated")


def _create_invoice(ctx, subscriber_id, product_id, period, due_at, price_idr):
    _unique(
        ctx,
        "invoice",
        {"subscriber_id": subscriber_id, "period": period, "product_id": product_id},
        "duplicate_invoice",
    )
    money(price_idr)
    return ctx.create(
        "invoice",
        {
            "subscriber_id": subscriber_id,
            "product_id": product_id,
            "period": period,
            "amount_idr": price_idr,
            "paid_idr": 0,
            "status": "open",
            "due_at": due_at,
            "created_by": ctx.actor,
        },
    )


def _net_order_amount(order):
    return max(0, order["price_idr"] - order.get("discount_idr", 0))


def _approved_orders_net(ctx, attribution_key, earner_id, period=None):
    total = 0
    for order in _bounded(ctx, "sales_order", {attribution_key: earner_id}):
        if order.get("status") != "approved":
            continue
        if period is not None and not str(order.get("approved_at", "")).startswith(period):
            continue
        total += _net_order_amount(order)
    return total


def _verified_paid_base(ctx, attribution_key, earner_id, period):
    # Commission is based on paid invoices, never an approved-but-unpaid order.
    orders=_bounded(ctx,'sales_order')
    total=0
    for invoice in _bounded(ctx,'invoice',{'period':period}):
        if invoice.get('status')!='paid' or type(invoice.get('paid_idr')) is not int or invoice['paid_idr']<=0:continue
        matches=[o for o in orders if o.get('status')=='approved' and o.get('subscriber_id')==invoice.get('subscriber_id') and o.get('product_id')==invoice.get('product_id')]
        owners={o.get(attribution_key) for o in matches}
        if owners=={earner_id}:total+=invoice['paid_idr']
    return total

def _nps_stats(responses):
    total = len(responses)
    promoters = sum(1 for r in responses if r >= 9)
    detractors = sum(1 for r in responses if r <= 6)
    passives = total - promoters - detractors
    promoter_pct = _div_half_up(promoters * 100, total)
    detractor_pct = _div_half_up(detractors * 100, total)
    delta = promoters - detractors
    if delta >= 0:
        nps_bps = (delta * 10000) // total
    else:
        nps_bps = -((-delta) * 10000 // total)
    return {
        "total": total,
        "promoters": promoters,
        "passives": passives,
        "detractors": detractors,
        "promoter_pct": promoter_pct,
        "detractor_pct": detractor_pct,
        "nps_score": (1 if delta>=0 else -1) * _div_half_up(abs(delta)*100,total),
        "score_rounding": "round_final_net_ratio_half_up",
        "nps_bps": nps_bps,
    }


_POS_WORDS = (
    "bagus", "cepat", "puas", "memuaskan", "ramah", "mantap", "baik",
    "good", "great", "fast", "friendly", "excellent", "helpful", "stable",
)
_NEG_WORDS = (
    "lambat", "jelek", "buruk", "kecewa", "mahal", "putus", "gangguan", "rusak",
    "slow", "bad", "poor", "down", "unhelpful", "rude", "outage", "drop",
)


def _sentiment(text):
    lowered = text.lower()
    positives = [w for w in _POS_WORDS if w in lowered]
    negatives = [w for w in _NEG_WORDS if w in lowered]
    if len(positives) > len(negatives):
        sentiment = "positive"
    elif len(negatives) > len(positives):
        sentiment = "negative"
    else:
        sentiment = "neutral"
    return sentiment, positives, negatives


def h_bill_invoice_generate(ctx, args):
    period = args["period"]
    _period(period)
    subscriber = _subscriber(ctx, args)
    _require_billable_subscriber(subscriber)
    product = _product(ctx, args)
    _require_live_product(product)
    _ts(args["due_at"])
    invoice = _create_invoice(
        ctx, subscriber["id"], product["id"], period, args["due_at"], product["price_idr"]
    )
    ctx.event(
        "bill.invoice.generated",
        {"invoice_id": invoice["id"], "subscriber_id": subscriber["id"], "period": period,
         "amount_idr": invoice["amount_idr"]},
    )
    return {"invoice": invoice}


def h_bill_cycle_run(ctx, args):
    period = args["period"]
    _period(period)
    due_at = args["due_at"]
    _ts(due_at)
    max_invoices = _pick(args, "max_invoices", LIST_CAP)
    require(type(max_invoices) is int and 1 <= max_invoices <= LIST_CAP, "invalid_max_invoices")
    if "product_id" in args and args["product_id"] is not None:
        product = _product(ctx, args)
        _require_live_product(product)
        products = [product]
    else:
        products = _bounded(ctx, "product", {"active": True})
    existing = {(row["subscriber_id"], row.get("product_id")) for row in _bounded(ctx, "invoice", {"period": period})}
    by_package = {}
    for subscriber in _bounded(ctx, "subscriber"):
        if subscriber.get("status") != "active":
            continue
        package = subscriber.get("package_id")
        if package:
            by_package.setdefault(package, []).append(subscriber)
    created = []
    skipped_existing = 0
    for product in products:
        for subscriber in by_package.get(product["id"], []):
            key = (subscriber["id"], product["id"])
            if key in existing:
                skipped_existing += 1
                continue
            if len(created) >= max_invoices:
                break
            invoice = _create_invoice(ctx, subscriber["id"], product["id"], period, due_at, product["price_idr"])
            created.append(invoice["id"])
            existing.add(key)
    ctx.event(
        "bill.cycle.ran",
        {"period": period, "invoices_created": len(created), "skipped_existing": skipped_existing},
    )
    return {
        "period": period,
        "products_scanned": len(products),
        "invoices_created": len(created),
        "created_invoice_ids": created,
        "skipped_existing": skipped_existing,
        "subscriber_scan_cap": LIST_CAP,
        "bounded_scan": True,
    }


def h_bill_payment_record(ctx, args):
    _subscriber(ctx,args)
    amount = money(args["amount_idr"])
    require(amount > 0, "invalid_amount_idr")
    reference = args["provider_reference"]
    _unique(ctx, "payment", {"provider_reference": reference}, "duplicate_provider_reference")
    payment = ctx.create(
        "payment",
        {
            "subscriber_id": args["subscriber_id"],
            "amount_idr": amount,
            "provider_reference": reference,
            "channel": args["channel"],
            "evidence": args.get("evidence", {}),
            "status": "recorded_unverified",
            "matched_invoice_ids": [],
            "allocated_idr": 0,
            "recorded_by": ctx.actor,
        },
    )
    ctx.event(
        "bill.payment.recorded_unverified",
        {"payment_id": payment["id"], "subscriber_id": payment["subscriber_id"], "amount_idr": amount},
    )
    return {
        "payment": payment,
        "note": "payment is recorded as UNVERIFIED; allocation runs only after verified confirmation via the payment adapter",
    }


def h_bill_payment_match(ctx, args):
    payment = _need(ctx, "payment", args["payment_id"], "payment_not_found")
    require(payment.get("status") == "verified", "payment_not_verified", 409)
    left = payment["amount_idr"] - payment.get("allocated_idr", 0)
    require(left > 0, "payment_fully_allocated", 409)
    if "invoice_ids" in args and args["invoice_ids"] is not None:
        require(len(args["invoice_ids"]) <= 100, "too_many_invoices")
        invoices = []
        for inv_id in args["invoice_ids"]:
            invoice = _need(ctx, "invoice", inv_id, "invoice_not_found")
            require(invoice["subscriber_id"] == payment["subscriber_id"], "invoice_subscriber_mismatch")
            require(invoice["status"] != "void", "invoice_void")
            invoices.append(invoice)
    else:
        invoices = [
            row
            for row in _bounded(ctx, "invoice", {"subscriber_id": payment["subscriber_id"]})
            if row["status"] in _INVOICE_OPEN
        ]
        invoices.sort(key=lambda r: (r["period"], r["created_at"], r["id"]))
    allocations = []
    matched_ids = list(payment.get("matched_invoice_ids", []))
    for invoice in invoices:
        if left <= 0:
            break
        invoice, remaining = _sync_invoice_status(ctx, invoice)
        if remaining <= 0:
            continue
        alloc = min(left, remaining)
        paid_after = invoice["paid_idr"] + alloc
        remaining_after = remaining - alloc
        status = "paid" if remaining_after <= 0 else "partially_paid"
        ctx.update("invoice", invoice["id"], {"paid_idr": paid_after, "status": status})
        allocations.append({"invoice_id": invoice["id"], "allocated_idr": alloc, "invoice_status": status})
        matched_ids.append(invoice["id"])
        left -= alloc
    updated_payment = ctx.update(
        "payment",
        payment["id"],
        {"allocated_idr": payment["amount_idr"] - left, "matched_invoice_ids": matched_ids},
    )
    ctx.event(
        "bill.payment.matched",
        {"payment_id": payment["id"], "allocated_idr": updated_payment["allocated_idr"], "invoices": len(allocations)},
    )
    return {
        "payment_id": payment["id"],
        "allocations": allocations,
        "unmatched_idr": left,
        "payment": updated_payment,
    }


def h_bill_arrears_calculate(ctx, args):
    subscriber = _subscriber(ctx, args)
    as_of = _now(ctx) if _pick(args, "as_of") is None else _ts(args["as_of"])
    buckets = {"not_due": 0, "1_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0}
    rows = []
    total = 0
    for invoice in _bounded(ctx, "invoice", {"subscriber_id": subscriber["id"]}):
        if invoice["status"] == "void":
            continue
        remaining, _status = _invoice_state(ctx, invoice)
        if remaining <= 0:
            continue
        due = _ts(invoice["due_at"])
        days_late = (as_of.date() - due.date()).days
        if days_late <= 0:
            bucket = "not_due"
        elif days_late <= 30:
            bucket = "1_30"
        elif days_late <= 60:
            bucket = "31_60"
        elif days_late <= 90:
            bucket = "61_90"
        else:
            bucket = "90_plus"
        buckets[bucket] += remaining
        total += remaining
        rows.append(
            {"invoice_id": invoice["id"], "period": invoice["period"], "remaining_idr": remaining,
             "due_at": invoice["due_at"], "days_late": days_late}
        )
    return {
        "subscriber_id": subscriber["id"],
        "total_arrears_idr": total,
        "invoice_count": len(rows),
        "aging_idr": buckets,
        "invoices": rows,
        "bounded_scan": True,
    }


def h_bill_dunning_run(ctx, args):
    subscriber = _subscriber(ctx, args)
    arrears = h_bill_arrears_calculate(ctx, {"subscriber_id": subscriber["id"]})
    require(arrears["total_arrears_idr"] > 0, "no_arrears", 409)
    invoice_id = _pick(args, "invoice_id")
    if invoice_id is not None:
        invoice = _need(ctx, "invoice", invoice_id, "invoice_not_found")
        require(invoice["subscriber_id"] == subscriber["id"], "invoice_subscriber_mismatch")
    step = ctx.create(
        "dunning_step",
        {
            "subscriber_id": subscriber["id"],
            "level": args["level"],
            "arrears_idr": arrears["total_arrears_idr"],
            "invoice_id": invoice_id,
            "subscriber_status_after": subscriber["status"],
            "performed_by": ctx.actor,
        },
    )
    if args["level"] == "suspend_request":
        ctx.event(
            "bill.dunning.suspend_request_recorded",
            {"subscriber_id": subscriber["id"], "dunning_step_id": step["id"], "network_effect": "none"},
        )
    return {
        "dunning_step": step,
        "arrears_idr": arrears["total_arrears_idr"],
        "note": "suspend_request is a local record only; subscriber status and network state are not changed",
    }


def h_bill_late_fee(ctx, args):
    invoice = _need(ctx, "invoice", args["invoice_id"], "invoice_not_found")
    rate_bps = args["rate_bps"]
    require(type(rate_bps) is int and 0 <= rate_bps <= 100000, "invalid_rate_bps")
    grace_days = _pick(args, "grace_days", 0)
    require(type(grace_days) is int and 0 <= grace_days <= 365, "invalid_grace_days")
    as_of = _now(ctx) if _pick(args, "as_of") is None else _ts(args["as_of"])
    remaining, _status = _invoice_state(ctx, invoice)
    if remaining <= 0:
        return {"calculation_only": True, "invoice_id": invoice["id"], "remaining_idr": 0, "days_late": 0,
                "late_fee_idr": 0}
    due = _ts(invoice["due_at"])
    days_late = (as_of.date() - due.date()).days - grace_days
    if days_late <= 0:
        return {"calculation_only": True, "invoice_id": invoice["id"], "remaining_idr": remaining, "days_late": 0,
                "late_fee_idr": 0}
    fee = min(remaining, _div_half_up(remaining * days_late * rate_bps, 10000))
    return {"calculation_only": True, "invoice_id": invoice["id"], "remaining_idr": remaining,
            "days_late": days_late, "late_fee_idr": fee}


def h_bill_discount_apply(ctx, args):
    invoice = _need(ctx, "invoice", args["invoice_id"], "invoice_not_found")
    amount = money(args["amount_idr"])
    require(amount > 0, "invalid_amount_idr")
    invoice, remaining = _sync_invoice_status(ctx, invoice)
    require(remaining > 0, "invoice_not_payable", 409)
    require(amount <= remaining, "discount_exceeds_balance")
    adjustment = ctx.create(
        "adjustment",
        {
            "subscriber_id": invoice["subscriber_id"],
            "invoice_id": invoice["id"],
            "direction": "credit",
            "category": "discount",
            "amount_idr": amount,
            "reason": args["reason"],
            "created_by": ctx.actor,
        },
    )
    invoice, remaining_after = _sync_invoice_status(ctx, invoice)
    ctx.event("bill.discount.applied", {"invoice_id": invoice["id"], "amount_idr": amount})
    return {"adjustment": adjustment, "invoice": invoice, "invoice_remaining_idr": remaining_after}


def h_bill_prorate_calculate(ctx, args):
    period = args["period"]
    _period(period)
    if _pick(args, "price_idr") is not None:
        price = money(args["price_idr"])
    elif _pick(args, "product_id") is not None:
        price = _product(ctx, args)["price_idr"]
    else:
        raise DomainError("price_source_required")
    active_from = _date(args["active_from"])
    active_to = _date(args["active_to"]) if _pick(args, "active_to") is not None else _period_bounds(period)[1]
    first, last = _period_bounds(period)
    require(first <= active_from <= last, "active_from_outside_period")
    require(first <= active_to <= last, "active_to_outside_period")
    require(active_from <= active_to, "invalid_active_range")
    days = _period_days(period)
    active_days = (active_to - active_from).days + 1
    charge = _div_half_up(price * active_days, days)
    return {
        "calculation_only": True,
        "period": period,
        "period_days": days,
        "price_idr": price,
        "active_days": active_days,
        "prorated_idr": charge,
        "rounding": "integer_round_half_up",
    }


def h_bill_deposit_manage(ctx, args):
    subscriber = _subscriber(ctx, args)
    amount = money(args["amount_idr"])
    require(amount > 0, "invalid_amount_idr")
    held = 0
    for row in _bounded(ctx, "deposit", {"subscriber_id": subscriber["id"]}):
        if row["action"] == "collect":
            held += row["amount_idr"]
        elif row["action"] == "release":
            held -= row["amount_idr"]
    if args["action"] == "release":
        require(amount <= held, "insufficient_deposit", 409)
        held -= amount
    else:
        held += amount
    record = ctx.create(
        "deposit",
        {
            "subscriber_id": subscriber["id"],
            "action": args["action"],
            "amount_idr": amount,
            "reason": args["reason"],
            "balance_after": held,
            "created_by": ctx.actor,
        },
    )
    return {"deposit": record, "held_idr": held}


def h_bill_refund_process(ctx, args):
    amount = money(args["amount_idr"])
    require(amount > 0, "invalid_amount_idr")
    payment_id = _pick(args, "payment_id")
    invoice_id = _pick(args, "invoice_id")
    require((payment_id is None) != (invoice_id is None), "refund_target_required")
    subscriber_id = None
    if payment_id is not None:
        payment = _need(ctx, "payment", payment_id, "payment_not_found")
        require(payment.get("status") == "verified", "payment_not_verified", 409)
        require(amount <= payment.get("allocated_idr", 0), "refund_exceeds_matched")
        subscriber_id = payment["subscriber_id"]
    else:
        invoice = _need(ctx, "invoice", invoice_id, "invoice_not_found")
        require(amount <= invoice["paid_idr"], "refund_exceeds_paid")
        subscriber_id = invoice["subscriber_id"]
    record = ctx.create(
        "refund",
        {
            "subscriber_id": subscriber_id,
            "payment_id": payment_id,
            "invoice_id": invoice_id,
            "amount_idr": amount,
            "reason": args["reason"],
            "status": "requested_local_record",
            "requested_by": ctx.actor,
        },
    )
    ctx.event("bill.refund.requested", {"refund_id": record["id"], "amount_idr": amount})
    return {
        "refund": record,
        "note": "refund is recorded as requested; execution requires a verified finance/payment connector",
    }


def h_bill_adjustment_create(ctx, args):
    subscriber = _subscriber(ctx, args)
    amount = money(args["amount_idr"])
    require(amount > 0, "invalid_amount_idr")
    invoice_id = _pick(args, "invoice_id")
    if invoice_id is not None:
        invoice = _need(ctx, "invoice", invoice_id, "invoice_not_found")
        require(invoice["subscriber_id"] == subscriber["id"], "invoice_subscriber_mismatch")
        invoice, remaining = _sync_invoice_status(ctx, invoice)
        if args["direction"] == "credit":
            require(amount <= remaining, "adjustment_exceeds_balance")
    adjustment = ctx.create(
        "adjustment",
        {
            "subscriber_id": subscriber["id"],
            "invoice_id": invoice_id,
            "direction": args["direction"],
            "category": _pick(args, "category", "correction"),
            "amount_idr": amount,
            "reason": args["reason"],
            "created_by": ctx.actor,
        },
    )
    remaining_after = None
    if invoice_id is not None and args["direction"] == "credit":
        invoice, remaining_after = _sync_invoice_status(ctx, invoice)
    ctx.event(
        "bill.adjustment.created",
        {"adjustment_id": adjustment["id"], "direction": args["direction"], "amount_idr": amount},
    )
    return {"adjustment": adjustment, "invoice_remaining_idr": remaining_after}


def h_bill_statement_generate(ctx, args):
    subscriber = _subscriber(ctx, args)
    period = args["period"]
    _period(period)
    invoices = []
    billed = paid = remaining_total = 0
    for invoice in _bounded(ctx, "invoice", {"subscriber_id": subscriber["id"], "period": period}):
        remaining, _status = _invoice_state(ctx, invoice)
        billed += invoice["amount_idr"]
        paid += invoice["paid_idr"]
        remaining_total += remaining
        invoices.append(
            {"invoice_id": invoice["id"], "product_id": invoice.get("product_id"), "amount_idr": invoice["amount_idr"],
             "paid_idr": invoice["paid_idr"], "remaining_idr": remaining, "status": invoice["status"]}
        )
    payment_rows = [p for p in _bounded(ctx, "payment", {"subscriber_id": subscriber["id"]})
                    if str(p.get("created_at", "")).startswith(period)]
    ctx.event("bill.statement.generated", {"subscriber_id": subscriber["id"], "period": period})
    return {
        "subscriber_id": subscriber["id"],
        "period": period,
        "invoices": invoices,
        "billed_idr": billed,
        "paid_idr": paid,
        "remaining_idr": remaining_total,
        "payments_recorded": {"count": len(payment_rows), "amount_idr": sum(p["amount_idr"] for p in payment_rows)},
        "bounded_scan": True,
    }


def h_bill_tax_calculate(ctx, args):
    base = money(args["base_idr"])
    rate_bps = args["rate_bps"]
    require(type(rate_bps) is int and 0 <= rate_bps <= 1000000, "invalid_rate_bps")
    _rid(args["policy_id"])
    _date(args["effective_date"])
    tax = _apply_bps(base, rate_bps)
    return {
        "calculation_only": True,
        "policy_id": args["policy_id"],
        "rate_bps": rate_bps,
        "effective_date": args["effective_date"],
        "base_idr": base,
        "basis": "paid_invoice_amount_only",
        "date_basis": "invoice.period",
        "tax_idr": tax,
        "gross_idr": base + tax,
        "note": "explicit policy required; no statutory rate is hardcoded and nothing is filed",
    }


def _prepaid_balance(ctx, subscriber_id):
    balance = 0
    for entry in _bounded(ctx, "prepaid_ledger", {"subscriber_id": subscriber_id}):
        if entry["entry_type"] == "topup":
            balance += entry["amount_idr"]
        else:
            balance -= entry["amount_idr"]
    return balance


def h_prepaid_balance_check(ctx, args):
    subscriber = _subscriber(ctx, args)
    entries = _bounded(ctx, "prepaid_ledger", {"subscriber_id": subscriber["id"]})
    return {
        "subscriber_id": subscriber["id"],
        "balance_idr": _prepaid_balance(ctx, subscriber["id"]),
        "entry_count": len(entries),
        "bounded_scan": True,
    }


def h_prepaid_topup(ctx, args):
    subscriber = _subscriber(ctx, args)
    amount = money(args["amount_idr"])
    require(amount > 0, "invalid_amount_idr")
    funding = args["funding"]
    require(funding.get("verified_by_adapter") is True, "funding_not_verified", 409)
    require(bool(funding.get("adapter_confirmation_id")), "funding_not_verified", 409)
    _ts(funding["confirmed_at"])
    balance_after = _prepaid_balance(ctx, subscriber["id"]) + amount
    entry = ctx.create(
        "prepaid_ledger",
        {
            "subscriber_id": subscriber["id"],
            "entry_type": "topup",
            "amount_idr": amount,
            "balance_after": balance_after,
            "funding_status": "verified_per_supplied_adapter_proof",
            "funding": funding,
            "created_by": ctx.actor,
        },
    )
    ctx.event("prepaid.topup", {"subscriber_id": subscriber["id"], "amount_idr": amount, "ledger_id": entry["id"]})
    return {"ledger": entry, "balance_idr": balance_after,
            "note": "proof authenticity is owned by the verified payment adapter boundary"}


def h_prepaid_deduct(ctx, args):
    subscriber = _subscriber(ctx, args)
    amount = money(args["amount_idr"])
    require(amount > 0, "invalid_amount_idr")
    balance = _prepaid_balance(ctx, subscriber["id"])
    require(balance >= amount, "insufficient_balance", 409)
    entry = ctx.create(
        "prepaid_ledger",
        {
            "subscriber_id": subscriber["id"],
            "entry_type": "deduct",
            "amount_idr": amount,
            "balance_after": balance - amount,
            "reason": args["reason"],
            "usage_ref": _pick(args, "usage_ref"),
            "created_by": ctx.actor,
        },
    )
    ctx.event("prepaid.deduct", {"subscriber_id": subscriber["id"], "amount_idr": amount, "ledger_id": entry["id"]})
    return {"ledger": entry, "balance_idr": balance - amount}


def _voucher_hash(code):
    # Internal prepared requests carry only the hash. Public intake rejects this prefix.
    if type(code) is str and _re.fullmatch(r"sha256:[0-9a-f]{64}",code):return code[7:]
    require(type(code) is str and _re.fullmatch(r"[A-Za-z0-9-]{8,64}", code), "invalid_voucher_code")
    return _hashlib.sha256(code.encode("utf-8")).hexdigest()


def h_voucher_generate(ctx, args):
    count = args["count"]
    require(type(count) is int and 1 <= count <= 200, "invalid_count")
    amount = money(args["amount_idr"])
    require(amount > 0, "invalid_amount_idr")
    expires_at = _pick(args, "expires_at")
    if expires_at is not None:
        _ts(expires_at)
    batch = ctx.create(
        "voucher_batch",
        {
            "name": _pick(args, "batch_name", "batch-" + ctx.now[:10]),
            "count": count,
            "amount_idr": amount,
            "expires_at": expires_at,
            "redeemed_count": 0,
            "expired_count": 0,
            "created_by": ctx.actor,
        },
    )
    codes = []
    for _ in range(count):
        code = "HRM-" + _secrets.token_hex(6).upper()
        ctx.create(
            "voucher",
            {
                "batch_id": batch["id"],
                "code_hash": _hashlib.sha256(code.encode("utf-8")).hexdigest(),
                "code_last4": code[-4:],
                "amount_idr": amount,
                "expires_at": expires_at,
                "status": "active",
            },
        )
        codes.append(code)
    ctx.event("voucher.batch_generated", {"batch_id": batch["id"], "count": count})
    return {
        "batch": batch,
        "codes": codes,
        "note": "plaintext codes are shown once; only SHA-256 hashes are stored",
    }


def _voucher_by_code(ctx, code):
    matches = ctx.list("voucher", {"code_hash": _voucher_hash(code)}, limit=1)
    return matches[0] if matches else None


def h_voucher_validate(ctx, args):
    voucher = _voucher_by_code(ctx, args["code"])
    if voucher is None:
        return {"status": "not_found", "voucher_id": None, "amount_idr": None, "expires_at": None,
                "code_last4": None}
    status = voucher["status"]
    if status == "active" and voucher.get("expires_at") is not None:
        if _ts(voucher["expires_at"]) <= _now(ctx):
            status = "expired"
    return {
        "voucher_id": voucher["id"],
        "status": status,
        "amount_idr": voucher["amount_idr"],
        "expires_at": voucher.get("expires_at"),
        "code_last4": voucher["code_last4"],
    }


def h_voucher_redeem(ctx, args):
    subscriber = _subscriber(ctx, args)
    voucher = _voucher_by_code(ctx, args["code"])
    require(voucher is not None, "voucher_not_found", 404)
    require(voucher["status"] == "active", "voucher_already_redeemed" if voucher["status"] == "redeemed" else "voucher_not_active", 409)
    if voucher.get("expires_at") is not None:
        require(_ts(voucher["expires_at"]) > _now(ctx), "voucher_expired", 409)
    updated = ctx.update(
        "voucher",
        voucher["id"],
        {"status": "redeemed", "redeemed_at": ctx.now, "redeemed_by": subscriber["id"]},
        expected_version=voucher["version"],
    )
    balance_after = _prepaid_balance(ctx, subscriber["id"]) + voucher["amount_idr"]
    ledger = ctx.create(
        "prepaid_ledger",
        {
            "subscriber_id": subscriber["id"],
            "entry_type": "topup",
            "amount_idr": voucher["amount_idr"],
            "balance_after": balance_after,
            "funding_status": "voucher_redeemed",
            "voucher_id": voucher["id"],
            "created_by": ctx.actor,
        },
    )
    batch = None
    if voucher.get("batch_id"):
        batch = ctx.get("voucher_batch", voucher["batch_id"])
        ctx.update("voucher_batch", batch["id"], {"redeemed_count": batch.get("redeemed_count", 0) + 1})
    ctx.event(
        "voucher.redeemed",
        {"voucher_id": voucher["id"], "subscriber_id": subscriber["id"], "amount_idr": voucher["amount_idr"]},
    )
    return {"voucher": updated, "ledger": ledger, "balance_idr": balance_after}


def h_voucher_distribute(ctx, args):
    dealer = _need(ctx, "dealer", args["dealer_id"], "dealer_not_found")
    require(dealer.get("status") == "active", "dealer_inactive", 409)
    batch = _need(ctx, "voucher_batch", args["batch_id"], "batch_not_found")
    max_count = args["max_count"]
    require(type(max_count) is int and 1 <= max_count <= 500, "invalid_max_count")
    assigned = []
    for voucher in _bounded(ctx, "voucher", {"batch_id": batch["id"], "status": "active"}):
        if len(assigned) >= max_count:
            break
        if voucher.get("dealer_id") is not None:
            continue
        ctx.update("voucher", voucher["id"], {"dealer_id": dealer["id"]})
        assigned.append(voucher["id"])
    ctx.event("voucher.distributed", {"batch_id": batch["id"], "dealer_id": dealer["id"], "count": len(assigned)})
    return {"batch_id": batch["id"], "dealer_id": dealer["id"], "distributed": len(assigned),
            "voucher_ids": assigned}


def h_voucher_expire_handle(ctx, args):
    batch_id = _pick(args, "batch_id")
    where = {"batch_id": batch_id, "status": "active"} if batch_id is not None else {"status": "active"}
    expired = []
    for voucher in _bounded(ctx, "voucher", where):
        if voucher.get("expires_at") is None:
            continue
        if _ts(voucher["expires_at"]) > _now(ctx):
            continue
        ctx.update("voucher", voucher["id"], {"status": "expired", "expired_at": ctx.now})
        expired.append(voucher["id"])
        if voucher.get("batch_id"):
            batch = ctx.get("voucher_batch", voucher["batch_id"])
            ctx.update("voucher_batch", batch["id"], {"expired_count": batch.get("expired_count", 0) + 1})
    ctx.event("voucher.expired", {"count": len(expired)})
    return {"expired_count": len(expired), "voucher_ids": expired, "bounded_scan": True}


def h_voucher_audit_report(ctx, args):
    batch = _need(ctx, "voucher_batch", args["batch_id"], "batch_not_found")
    threshold = _pick(args, "suspicious_threshold", 5)
    require(type(threshold) is int and 1 <= threshold <= 100, "invalid_threshold")
    vouchers = _bounded(ctx, "voucher", {"batch_id": batch["id"]})
    totals = {"active": 0, "redeemed": 0, "expired": 0}
    per_redeemer = {}
    for voucher in vouchers:
        status = voucher["status"]
        totals[status] = totals.get(status, 0) + 1
        if status == "redeemed":
            redeemer = voucher.get("redeemed_by")
            if redeemer:
                per_redeemer[redeemer] = per_redeemer.get(redeemer, 0) + 1
    suspicious = [
        {"redeemed_by": key, "count": value} for key, value in sorted(per_redeemer.items()) if value >= threshold
    ]
    total = len(vouchers)
    return {
        "batch_id": batch["id"],
        "total": total,
        "totals": totals,
        "redeem_rate_bps": (totals["redeemed"] * 10000 // total) if total else 0,
        "suspicious_redeemers": suspicious,
        "bounded_scan": True,
    }


def h_product_create(ctx, args):
    _unique(ctx, "product", {"name": args["name"]}, "duplicate_product_name")
    product = ctx.create(
        "product",
        {
            "name": args["name"],
            "price_idr": money(args["price_idr"]),
            "speed_mbps": _pick(args, "speed_mbps"),
            "segment_target": _pick(args, "segment_target", "all"),
            "description": _pick(args, "description"),
            "billing_period": "monthly",
            "active": True,
            "lifecycle_status": "active",
            "is_bundle": False,
            "created_by": ctx.actor,
        },
    )
    ctx.event("product.created", {"product_id": product["id"], "name": product["name"]})
    return {"product": product}


def h_product_update(ctx, args):
    product = _product(ctx, args)
    _require_live_product(product)
    changes = {}
    for field in ("name", "description", "speed_mbps", "segment_target"):
        if field in args and args[field] is not None:
            changes[field] = args[field]
    require(bool(changes), "no_changes")
    if "name" in changes and changes["name"] != product["name"]:
        _unique(ctx, "product", {"name": changes["name"]}, "duplicate_product_name")
    updated = ctx.update("product", product["id"], changes)
    return {"product": updated}


def h_product_pricing_set(ctx, args):
    product = _product(ctx, args)
    _require_live_product(product)
    new_price = money(args["price_idr"])
    old_price = product["price_idr"]
    ctx.update("product", product["id"], {"price_idr": new_price})
    history = ctx.create(
        "pricing_history",
        {
            "product_id": product["id"],
            "old_price_idr": old_price,
            "new_price_idr": new_price,
            "effective_from": _pick(args, "effective_from", ctx.now),
            "reason": args["reason"],
            "changed_by": ctx.actor,
        },
    )
    ctx.event(
        "product.pricing.changed",
        {"product_id": product["id"], "old_price_idr": old_price, "new_price_idr": new_price},
    )
    return {"product_id": product["id"], "old_price_idr": old_price, "new_price_idr": new_price,
            "pricing_history": history}


def h_product_bundling(ctx, args):
    components = args["component_product_ids"]
    require(len(set(components)) == len(components), "duplicate_components")
    resolved = []
    for component_id in components:
        component = _need(ctx, "product", component_id, "product_not_found")
        _require_live_product(component)
        require(component.get("is_bundle") is not True, "component_is_bundle")
        resolved.append(component["id"])
    _unique(ctx, "product", {"name": args["name"]}, "duplicate_product_name")
    product = ctx.create(
        "product",
        {
            "name": args["name"],
            "price_idr": money(args["price_idr"]),
            "component_product_ids": resolved,
            "description": _pick(args, "description"),
            "billing_period": "monthly",
            "active": True,
            "lifecycle_status": "active",
            "is_bundle": True,
            "created_by": ctx.actor,
        },
    )
    ctx.event("product.bundle.created", {"product_id": product["id"], "components": resolved})
    return {"product": product}


def h_product_promo_create(ctx, args):
    has_bps = _pick(args, "discount_bps") is not None
    has_amount = _pick(args, "discount_idr") is not None
    require(has_bps != has_amount, "promo_discount_required")
    if has_bps:
        require(type(args["discount_bps"]) is int and 1 <= args["discount_bps"] <= 10000, "invalid_discount_bps")
    else:
        require(money(args["discount_idr"]) > 0, "invalid_amount_idr")
    valid_from = _date(args["valid_from"])
    valid_to = _date(args["valid_to"])
    require(valid_from < valid_to, "invalid_promo_window")
    if _pick(args, "product_id") is not None:
        _product(ctx, args)
    promo = ctx.create(
        "promo",
        {
            "name": args["name"],
            "product_id": _pick(args, "product_id"),
            "discount_bps": args.get("discount_bps"),
            "discount_idr": args.get("discount_idr"),
            "valid_from": args["valid_from"],
            "valid_to": args["valid_to"],
            "max_redemptions": _pick(args, "max_redemptions"),
            "status": "active",
            "created_by": ctx.actor,
        },
    )
    return {"promo": promo}


def h_product_promo_validate(ctx, args):
    promo = _need(ctx, "promo", args["promo_id"], "promo_not_found")
    subscriber = _subscriber(ctx, args)
    today = _now(ctx).date()
    checks = []
    window_ok = _date(promo["valid_from"]) <= today < _date(promo["valid_to"])
    checks.append({"rule": "promo_window", "passed": window_ok})
    status_ok = subscriber["status"] in ("active", "pending")
    checks.append({"rule": "subscriber_status", "passed": status_ok})
    product_ok = True
    if promo.get("product_id") is not None:
        try:
            product = ctx.get("product", promo["product_id"])
            product_ok = product.get("active") is True
        except DomainError:
            product_ok = False
    checks.append({"rule": "product_active", "passed": product_ok})
    redeemed = len(_bounded(ctx, "promo_redemption", {"promo_id": promo["id"]}))
    quota_ok = True
    if promo.get("max_redemptions") is not None:
        quota_ok = redeemed < promo["max_redemptions"]
    checks.append({"rule": "promo_quota", "passed": quota_ok})
    eligible = all(check["passed"] for check in checks)
    return {"promo_id": promo["id"], "subscriber_id": subscriber["id"], "eligible": eligible, "checks": checks,
            "redemptions_recorded": redeemed}


def h_product_lifecycle_manage(ctx, args):
    product = _product(ctx, args)
    action = args["action"]
    if action == "retire":
        require(product.get("active") is True, "invalid_lifecycle_transition", 409)
        updated = ctx.update("product", product["id"], {"active": False, "lifecycle_status": "retired"})
    elif action == "reactivate":
        require(product.get("active") is False, "invalid_lifecycle_transition", 409)
        updated = ctx.update("product", product["id"], {"active": True, "lifecycle_status": "active"})
    else:
        raise DomainError("invalid_lifecycle_transition", 409)
    ctx.event("product.lifecycle.changed", {"product_id": product["id"], "action": action})
    return {"product": updated}


def h_product_eligibility_check(ctx, args):
    subscriber = _subscriber(ctx, args)
    product = _product(ctx, args)
    checks = [
        {"rule": "product_active", "passed": product.get("active") is True},
        {"rule": "subscriber_not_terminated", "passed": subscriber["status"] != "terminated"},
    ]
    target = product.get("segment_target")
    segment_ok = target in (None, "all") or target == subscriber.get("segment")
    checks.append({"rule": "segment_match", "passed": segment_ok})
    eligible = all(check["passed"] for check in checks)
    return {"subscriber_id": subscriber["id"], "product_id": product["id"], "eligible": eligible, "checks": checks}


def h_product_feature_attaching(ctx, args):
    product = _product(ctx, args)
    _require_live_product(product)
    _unique(ctx, "product_feature", {"product_id": product["id"], "feature_name": args["feature_name"]},
            "duplicate_feature")
    feature = ctx.create(
        "product_feature",
        {
            "product_id": product["id"],
            "feature_name": args["feature_name"],
            "price_idr": money(args["price_idr"]),
            "active": True,
            "created_by": ctx.actor,
        },
    )
    return {"product_feature": feature}


def h_product_compatibility_check(ctx, args):
    product_a = _need(ctx, "product", args["product_id_a"], "product_not_found")
    product_b = _need(ctx, "product", args["product_id_b"], "product_not_found")
    checks = [
        {"rule": "distinct_products", "passed": product_a["id"] != product_b["id"]},
        {"rule": "both_active",
         "passed": product_a.get("active") is True and product_b.get("active") is True},
    ]
    nested = product_a.get("is_bundle") is True and product_b.get("is_bundle") is True
    checks.append({"rule": "no_nested_bundle", "passed": not nested})
    already = (product_a.get("is_bundle") is True and product_b["id"] in product_a.get("component_product_ids", [])) or (
        product_b.get("is_bundle") is True and product_a["id"] in product_b.get("component_product_ids", [])
    )
    checks.append({"rule": "not_already_component", "passed": not already})
    compatible = all(check["passed"] for check in checks)
    return {"compatible": compatible, "checks": checks}


def h_sub_create(ctx, args):
    external = _pick(args, "external_customer_id")
    if external is not None:
        _rid(external)
        _unique(ctx, "subscriber", {"external_customer_id": external}, "duplicate_external_customer")
    if _pick(args, "package_id") is not None:
        _need(ctx, "product", args["package_id"], "product_not_found")
    subscriber = ctx.create(
        "subscriber",
        {
            "name": args["name"],
            "status": "pending",
            "segment": _pick(args, "segment", "residential"),
            "contact_email": _pick(args, "contact_email"),
            "contact_phone": _pick(args, "contact_phone"),
            "address": _pick(args, "address"),
            "external_customer_id": external,
            "package_id": _pick(args, "package_id"),
            "created_by": ctx.actor,
        },
    )
    ctx.event("sub.created", {"subscriber_id": subscriber["id"], "status": subscriber["status"]})
    return {
        "subscriber": subscriber,
        "note": "created pending; KYC and network activation are separate local/adapter steps and are NOT performed here",
    }


def h_sub_profile_update(ctx, args):
    subscriber = _subscriber(ctx, args)
    require(subscriber["status"] != "terminated", "subscriber_terminated", 409)
    changes = {}
    for field in ("name", "contact_email", "contact_phone", "address", "notes"):
        if field in args and args[field] is not None:
            changes[field] = args[field]
    require(bool(changes), "no_changes")
    updated = ctx.update("subscriber", subscriber["id"], changes)
    return {"subscriber": updated}


def h_sub_kyc_verify(ctx, args):
    subscriber = _subscriber(ctx, args)
    evidence = args["evidence"]
    case = ctx.create(
        "kyc_case",
        {
            "subscriber_id": subscriber["id"],
            "status": "pending_manual_verification",
            "evidence": evidence,
            "verified": False,
            "reviewed_by": None,
            "requested_by": ctx.actor,
        },
    )
    ctx.event(
        "sub.kyc.pending_manual_verification",
        {"subscriber_id": subscriber["id"], "kyc_case_id": case["id"]},
    )
    return {
        "kyc_case": case,
        "verified": False,
        "note": "identity confirmation requires an authorized verification connector; evidence is recorded, never self-verified",
    }


def h_sub_status_manage(ctx, args):
    subscriber = _subscriber(ctx, args)
    new_status = args["new_status"]
    allowed = {
        "pending": ("active", "terminated"),
        "active": ("suspended", "terminated"),
        "suspended": ("active", "terminated"),
        "terminated": (),
    }
    require(new_status in allowed.get(subscriber["status"], ()), "invalid_status_transition", 409)
    if new_status == "active":
        require(args.get("local_activation_confirmed") is True, "activation_confirmation_required")
    updated = ctx.update(
        "subscriber",
        subscriber["id"],
        {"status": new_status, "status_changed_at": ctx.now, "status_changed_by": ctx.actor},
    )
    ctx.event(
        "sub.status.changed",
        {"subscriber_id": subscriber["id"], "from": subscriber["status"], "to": new_status,
         "network_effect": "none_local_record_only"},
    )
    return {
        "subscriber": updated,
        "network_effect": "none; live activation/suspension requires the verified network connector owned by root",
    }


def h_sub_contact_manage(ctx, args):
    subscriber = _subscriber(ctx, args)
    action = args["action"]
    if action == "add":
        require(subscriber["status"] != "terminated", "subscriber_terminated", 409)
        require(bool(args.get("name")), "contact_name_required")
        is_primary = _pick(args, "is_primary", False)
        if is_primary:
            for contact in _bounded(ctx, "subscriber_contact",
                                    {"subscriber_id": subscriber["id"], "is_primary": True}):
                ctx.update("subscriber_contact", contact["id"], {"is_primary": False})
        contact = ctx.create(
            "subscriber_contact",
            {
                "subscriber_id": subscriber["id"],
                "name": args["name"],
                "phone": _pick(args, "phone"),
                "email": _pick(args, "email"),
                "role": _pick(args, "role", "other"),
                "is_primary": bool(is_primary),
                "status": "active",
            },
        )
        return {"contact": contact}
    require(args.get("contact_id") is not None, "contact_id_required")
    contact = _need(ctx, "subscriber_contact", args["contact_id"], "contact_not_found")
    require(contact["subscriber_id"] == subscriber["id"], "contact_subscriber_mismatch")
    require(contact.get("status") != "removed", "contact_removed", 409)
    if action == "update":
        changes = {}
        for field in ("name", "phone", "email", "role"):
            if field in args and args[field] is not None:
                changes[field] = args[field]
        require(bool(changes), "no_changes")
        return {"contact": ctx.update("subscriber_contact", contact["id"], changes)}
    if action == "set_primary":
        for other in _bounded(ctx, "subscriber_contact", {"subscriber_id": subscriber["id"], "is_primary": True}):
            if other["id"] != contact["id"]:
                ctx.update("subscriber_contact", other["id"], {"is_primary": False})
        return {"contact": ctx.update("subscriber_contact", contact["id"], {"is_primary": True})}
    if action == "remove":
        return {"contact": ctx.update("subscriber_contact", contact["id"], {"status": "removed"})}
    raise DomainError("invalid_contact_action")


def h_sub_address_verify(ctx, args):
    subscriber = _subscriber(ctx, args)
    record = ctx.create(
        "address_check",
        {
            "subscriber_id": subscriber["id"],
            "address": args["address"],
            "evidence_ref": _pick(args, "evidence_ref"),
            "status": "recorded_pending_coverage_check",
            "coverage_result": None,
            "requested_by": ctx.actor,
        },
    )
    return {
        "address_check": record,
        "note": "coverage determination belongs to the operations/GIS worker with real evidence; only the request is recorded",
    }


def _add_months_date(day, count):
    total = day.year * 12 + (day.month - 1) + count
    year, month = total // 12, total % 12 + 1
    last_day = 29 if (month == 2 and _is_leap(year)) else _MONTH_DAYS[month - 1]
    return _dt.date(year, month, min(day.day, last_day))


def h_sub_contract_manage(ctx, args):
    subscriber = _subscriber(ctx, args)
    action = args["action"]
    if action == "create":
        require(subscriber["status"] != "terminated", "subscriber_terminated", 409)
        months = args["months"]
        require(type(months) is int and 1 <= months <= 36, "invalid_months")
        active_contracts = [
            c for c in _bounded(ctx, "contract", {"subscriber_id": subscriber["id"]}) if c.get("status") == "active"
        ]
        require(not active_contracts, "contract_already_active", 409)
        start = _date(args["start_at"]) if _pick(args, "start_at") is not None else _now(ctx).date()
        end = _add_months_date(start, months) - _dt.timedelta(days=1)
        penalty = _pick(args, "penalty_idr")
        if penalty is not None:
            money(penalty)
        contract = ctx.create(
            "contract",
            {
                "subscriber_id": subscriber["id"],
                "months": months,
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "penalty_idr": penalty,
                "status": "active",
                "created_by": ctx.actor,
            },
        )
        return {"contract": contract}
    require(args.get("contract_id") is not None, "contract_id_required")
    contract = _need(ctx, "contract", args["contract_id"], "contract_not_found")
    require(contract["subscriber_id"] == subscriber["id"], "contract_subscriber_mismatch")
    require(contract.get("status") == "active", "invalid_contract_transition", 409)
    updated = ctx.update("contract", contract["id"], {"status": "ended", "ended_at": ctx.now})
    return {"contract": updated}


def h_sub_document_store(ctx, args):
    subscriber = _subscriber(ctx, args)
    document = ctx.create(
        "subscriber_document",
        {
            "subscriber_id": subscriber["id"],
            "doc_type": args["doc_type"],
            "doc_ref": args["doc_ref"],
            "content_sha256": _pick(args, "content_sha256"),
            "notes": _pick(args, "notes"),
            "status": "received_unverified",
            "recorded_by": ctx.actor,
        },
    )
    return {"document": document, "note": "metadata only; files are never opened or stored and nothing is verified"}


def h_sub_segmentation(ctx, args):
    subscriber = _subscriber(ctx, args)
    require(subscriber["status"] != "terminated", "subscriber_terminated", 409)
    updated = ctx.update("subscriber", subscriber["id"], {"segment": args["segment"]})
    ctx.event("sub.segmentation.changed", {"subscriber_id": subscriber["id"], "segment": args["segment"]})
    return {"subscriber": updated}


def h_sub_lifetime_value(ctx, args):
    subscriber = _subscriber(ctx, args)
    expected_months = args["expected_months"]
    require(type(expected_months) is int and 1 <= expected_months <= 120, "invalid_expected_months")
    paid = 0
    for invoice in _bounded(ctx, "invoice", {"subscriber_id": subscriber["id"]}):
        paid += invoice["paid_idr"]
    created = _ts(subscriber["created_at"])
    months_observed = max(1, (_now(ctx).date() - created.date()).days // 30)
    arpu = paid // months_observed
    return {
        "heuristic": True,
        "subscriber_id": subscriber["id"],
        "paid_to_date_idr": paid,
        "months_observed": months_observed,
        "arpu_idr": arpu,
        "expected_months": expected_months,
        "projected_clv_idr": arpu * expected_months,
        "bounded_scan": True,
    }


def h_sub_churn_predict(ctx, args):
    subscriber = _subscriber(ctx, args)
    signals = {}
    score = 0
    arrears = h_bill_arrears_calculate(ctx, {"subscriber_id": subscriber["id"]})
    signals["has_arrears"] = arrears["total_arrears_idr"] > 0
    if signals["has_arrears"]:
        score += 40
    payments = _bounded(ctx, "payment", {"subscriber_id": subscriber["id"]})
    if payments:
        last_payment = max(_ts(p["created_at"]) for p in payments)
        days_since = (_now(ctx) - last_payment).days
        signals["days_since_last_payment"] = days_since
        if days_since > 90:
            score += 30
    else:
        signals["days_since_last_payment"] = None
        if (_now(ctx) - _ts(subscriber["created_at"])).days > 90:
            score += 30
    open_tickets = [
        t for t in _bounded(ctx, "ticket", {"subscriber_id": subscriber["id"]}) if t.get("status") != "resolved"
    ]
    signals["open_tickets"] = len(open_tickets)
    if open_tickets:
        score += 15
    signals["status_suspended"] = subscriber["status"] == "suspended"
    if signals["status_suspended"]:
        score += 15
    score = min(score, 100)
    category = "low" if score < 30 else ("medium" if score < 60 else "high")
    return {
        "heuristic": True,
        "subscriber_id": subscriber["id"],
        "risk_score": score,
        "category": category,
        "signals": signals,
        "bounded_scan": True,
    }


def h_sub_merge_duplicate(ctx, args):
    survivor = _need(ctx, "subscriber", args["survivor_id"], "subscriber_not_found")
    duplicate = _need(ctx, "subscriber", args["duplicate_id"], "subscriber_not_found")
    require(duplicate["id"] != survivor["id"], "cannot_merge_self")
    require(duplicate.get("status") != "terminated" and duplicate.get("merged_into") is None,
            "duplicate_already_merged", 409)
    moved = {}
    for kind in ("invoice", "payment", "subscriber_contact", "lead"):
        rows = _bounded(ctx, kind, {"subscriber_id": duplicate["id"]})
        require(len(rows) < LIST_CAP, "merge_scope_exceeded", 413)
        for row in rows:
            ctx.update(kind, row["id"], {"subscriber_id": survivor["id"]})
        moved[kind] = len(rows)
    updated = ctx.update(
        "subscriber",
        duplicate["id"],
        {"status": "terminated", "merged_into": survivor["id"], "merged_at": ctx.now,
         "merge_reason": args["reason"]},
    )
    ctx.event(
        "sub.merge.duplicate",
        {"survivor_id": survivor["id"], "duplicate_id": duplicate["id"], "moved": moved},
    )
    return {"survivor_id": survivor["id"], "duplicate": updated, "moved_counts": moved}


def h_sales_lead_capture(ctx, args):
    if _pick(args, "campaign_id") is not None:
        _need(ctx, "campaign", args["campaign_id"], "campaign_not_found")
    if _pick(args, "product_id") is not None:
        _product(ctx, args)
    lead = ctx.create(
        "lead",
        {
            "source": args["source"],
            "contact_name": args["contact_name"],
            "contact_ref": args["contact_ref"],
            "campaign_id": _pick(args, "campaign_id"),
            "product_id": _pick(args, "product_id"),
            "notes": _pick(args, "notes"),
            "subscriber_id": None,
            "status": "new",
            "captured_by": ctx.actor,
        },
    )
    return {"lead": lead}


def h_sales_lead_qualify(ctx, args):
    lead = _need(ctx, "lead", args["lead_id"], "lead_not_found")
    require(lead.get("status") == "new", "lead_already_processed", 409)
    updated = ctx.update(
        "lead",
        lead["id"],
        {
            "status": args["decision"],
            "qualification_reason": args["reason"],
            "coverage_evidence_ref": _pick(args, "coverage_evidence_ref"),
            "qualified_by": ctx.actor,
            "qualified_at": ctx.now,
        },
    )
    return {"lead": updated,
            "note": "records a human qualification decision; coverage truthfulness depends on operations evidence"}


def h_sales_quote_generate(ctx, args):
    lead_id = _pick(args, "lead_id")
    subscriber_id = _pick(args, "subscriber_id")
    require((lead_id is None) != (subscriber_id is None), "quote_target_required")
    if lead_id is not None:
        target = _need(ctx, "lead", lead_id, "lead_not_found")
        target_key = "lead_id"
    else:
        target = _subscriber(ctx, args)
        target_key = "subscriber_id"
    product = _product(ctx, args)
    _require_live_product(product)
    install_fee = money(_pick(args, "install_fee_idr", 0))
    discount = money(_pick(args, "discount_idr", 0))
    require(discount <= product["price_idr"], "discount_exceeds_price")
    valid_days = _pick(args, "valid_days", 30)
    require(type(valid_days) is int and 1 <= valid_days <= 90, "invalid_valid_days")
    valid_until = (_now(ctx) + _dt.timedelta(days=valid_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    items = [{"description": product["name"], "qty": 1, "unit_price_idr": product["price_idr"]}]
    if install_fee > 0:
        items.append({"description": "install_fee", "qty": 1, "unit_price_idr": install_fee})
    total = max(0, product["price_idr"] + install_fee - discount)
    quote = ctx.create(
        "quote",
        {
            target_key: target["id"],
            "product_id": product["id"],
            "items": items,
            "recurring_price_idr": product["price_idr"],
            "install_fee_idr": install_fee,
            "discount_idr": discount,
            "total_idr": total,
            "valid_until": valid_until,
            "status": "generated",
            "created_by": ctx.actor,
        },
    )
    return {"quote": quote}


def h_sales_order_create(ctx, args):
    subscriber = _subscriber(ctx, args)
    _require_billable_subscriber(subscriber)
    product = _product(ctx, args)
    _require_live_product(product)
    quote_id = _pick(args, "quote_id")
    discount = 0
    if quote_id is not None:
        quote = _need(ctx, "quote", quote_id, "quote_not_found")
        require(quote.get("subscriber_id") == subscriber["id"], "quote_subscriber_mismatch")
        require(quote.get("product_id") == product["id"], "quote_product_mismatch")
        discount = quote.get("discount_idr", 0)
    if _pick(args, "dealer_id") is not None:
        dealer = _need(ctx, "dealer", args["dealer_id"], "dealer_not_found")
        require(dealer.get("status") == "active", "dealer_inactive", 409)
    if _pick(args, "sales_id") is not None:
        _rid(args["sales_id"])
    order = ctx.create(
        "sales_order",
        {
            "subscriber_id": subscriber["id"],
            "product_id": product["id"],
            "quote_id": quote_id,
            "sales_id": _pick(args, "sales_id"),
            "dealer_id": _pick(args, "dealer_id"),
            "price_idr": product["price_idr"],
            "discount_idr": discount,
            "status": "draft",
            "created_by": ctx.actor,
        },
    )
    ctx.event("sales.order.created", {"order_id": order["id"], "subscriber_id": subscriber["id"]})
    return {"order": order}


def h_sales_order_approve(ctx, args):
    order = _need(ctx, "sales_order", args["order_id"], "order_not_found")
    require(order.get("status") == "draft", "invalid_order_transition", 409)
    subscriber = ctx.get("subscriber", order["subscriber_id"])
    _require_billable_subscriber(subscriber)
    product = ctx.get("product", order["product_id"])
    _require_live_product(product)
    updated = ctx.update(
        "sales_order",
        order["id"],
        {"status": "approved", "approved_at": ctx.now, "approved_by": ctx.actor},
    )
    ctx.event("sales.order.approved", {"order_id": order["id"], "approved_by": ctx.actor})
    return {"order": updated}


def h_sales_commission_calc(ctx, args):
    period = args["period"]
    _period(period)
    earner_type = args["earner_type"]
    earner_id = _rid(args["earner_id"])
    if earner_type == "dealer":
        dealer = _need(ctx, "dealer", earner_id, "dealer_not_found")
        default_bps = dealer.get("commission_bps", 0)
    else:
        default_bps = None
    rate_bps = _pick(args, "rate_bps", default_bps)
    require(type(rate_bps) is int and 0 <= rate_bps <= 10000, "rate_bps_required")
    key = "dealer_id" if earner_type == "dealer" else "sales_id"
    base = _verified_paid_base(ctx, key, earner_id, period)
    return {
        "calculation_only": True,
        "period": period,
        "earner_type": earner_type,
        "earner_id": earner_id,
        "base_idr": base,
        "basis": "paid_invoice_amount_only",
        "date_basis": "invoice.period",
        "rate_bps": rate_bps,
        "commission_idr": _apply_bps(base, rate_bps),
        "rounding": "integer_round_half_up",
        "bounded_scan": True,
    }


def h_sales_target_track(ctx, args):
    period = args["period"]
    _period(period)
    sales_id = _rid(args["sales_id"])
    target = money(args["target_idr"])
    require(target > 0, "invalid_target")
    actual = _approved_orders_net(ctx, "sales_id", sales_id, period)
    return {
        "sales_id": sales_id,
        "period": period,
        "target_idr": target,
        "actual_idr": actual,
        "attainment_bps": actual * 10000 // target,
        "gap_idr": max(0, target - actual),
        "bounded_scan": True,
    }


def h_sales_funnel_analyze(ctx, args):
    period = _pick(args, "period")
    leads = _bounded(ctx, "lead")
    quotes = _bounded(ctx, "quote")
    orders = _bounded(ctx, "sales_order")
    if period is not None:
        _period(period)
        leads = [l for l in leads if str(l.get("created_at", "")).startswith(period)]
        quotes = [q for q in quotes if str(q.get("created_at", "")).startswith(period)]
        orders = [o for o in orders if str(o.get("created_at", "")).startswith(period)]
    lead_counts = {"new": 0, "qualified": 0, "disqualified": 0}
    for lead in leads:
        status = lead.get("status", "new")
        lead_counts[status] = lead_counts.get(status, 0) + 1
    approved = sum(1 for o in orders if o.get("status") == "approved")
    return {
        "period": period,
        "leads": {"total": len(leads), "by_status": lead_counts},
        "quotes": len(quotes),
        "orders": {"total": len(orders), "approved": approved},
        "lead_to_order_bps": approved * 10000 // len(leads) if leads else 0,
        "bounded_scan": True,
    }


def h_sales_campaign_track(ctx, args):
    action = args["action"]
    if action == "create":
        starts = _date(args["starts_at"])
        ends = _date(args["ends_at"])
        require(starts < ends, "invalid_campaign_window")
        campaign = ctx.create(
            "campaign",
            {
                "name": args["name"],
                "channel": args["channel"],
                "starts_at": args["starts_at"],
                "ends_at": args["ends_at"],
                "budget_idr": money(args["budget_idr"]),
                "status": "active",
                "created_by": ctx.actor,
            },
        )
        return {"campaign": campaign}
    require(args.get("campaign_id") is not None, "campaign_id_required")
    campaign = _need(ctx, "campaign", args["campaign_id"], "campaign_not_found")
    leads = _bounded(ctx, "lead", {"campaign_id": campaign["id"]})
    lead_ids = {lead["id"] for lead in leads}
    quotes = [q for q in _bounded(ctx, "quote") if q.get("lead_id") in lead_ids]
    quote_ids = {quote["id"] for quote in quotes}
    orders = [o for o in _bounded(ctx, "sales_order") if o.get("quote_id") in quote_ids]
    approved = [order for order in orders if order.get("status") == "approved"]
    return {
        "campaign": {"id": campaign["id"], "name": campaign["name"], "budget_idr": campaign["budget_idr"]},
        "leads": len(leads),
        "quotes": len(quotes),
        "orders_approved": len(approved),
        "approved_revenue_idr": sum(_net_order_amount(order) for order in approved),
        "bounded_scan": True,
    }


def h_sales_referral_manage(ctx, args):
    action = args["action"]
    if action == "record":
        require(args.get("referrer_subscriber_id") is not None and args.get("referred_contact_ref") is not None,
                "referral_fields_required")
        referrer = _need(ctx, "subscriber", args["referrer_subscriber_id"], "subscriber_not_found")
        require(referrer["status"] != "terminated", "subscriber_terminated", 409)
        referral = ctx.create(
            "referral",
            {
                "referrer_subscriber_id": referrer["id"],
                "referred_contact_ref": args["referred_contact_ref"],
                "notes": _pick(args, "notes"),
                "reward_idr": 0,
                "status": "recorded",
                "created_by": ctx.actor,
            },
        )
        return {"referral": referral}
    require(args.get("referral_id") is not None and args.get("reward_idr") is not None, "referral_fields_required")
    referral = _need(ctx, "referral", args["referral_id"], "referral_not_found")
    require(referral.get("status") == "recorded", "invalid_referral_transition", 409)
    reward = money(args["reward_idr"])
    require(reward > 0, "invalid_amount_idr")
    updated = ctx.update("referral", referral["id"], {"status": "reward_approved", "reward_idr": reward})
    return {"referral": updated,
            "note": "reward approval is a local record; payout requires a finance connector"}


def h_dealer_register(ctx, args):
    _unique(ctx, "dealer", {"name": args["name"]}, "duplicate_dealer_name")
    dealer = ctx.create(
        "dealer",
        {
            "name": args["name"],
            "region": args["region"],
            "commission_bps": _pick(args, "commission_bps", 0),
            "status": "active",
            "created_by": ctx.actor,
        },
    )
    return {"dealer": dealer}


def h_dealer_commission(ctx, args):
    period = args["period"]
    _period(period)
    dealer = _need(ctx, "dealer", args["dealer_id"], "dealer_not_found")
    base = _verified_paid_base(ctx, "dealer_id", dealer["id"], period)
    rate = dealer.get("commission_bps", 0)
    return {
        "calculation_only": True,
        "dealer_id": dealer["id"],
        "period": period,
        "base_idr": base,
        "basis": "paid_invoice_amount_only",
        "date_basis": "invoice.period",
        "commission_bps": rate,
        "commission_idr": _apply_bps(base, rate),
        "bounded_scan": True,
    }


def h_dealer_settlement(ctx, args):
    period = args["period"]
    _period(period)
    dealer = _need(ctx, "dealer", args["dealer_id"], "dealer_not_found")
    _unique(ctx, "dealer_settlement", {"dealer_id": dealer["id"], "period": period}, "settlement_already_recorded")
    base = _verified_paid_base(ctx, "dealer_id", dealer["id"], period)
    rate = dealer.get("commission_bps", 0)
    settlement = ctx.create(
        "dealer_settlement",
        {
            "dealer_id": dealer["id"],
            "period": period,
            "base_idr": base,
        "basis": "paid_invoice_amount_only",
        "date_basis": "invoice.period",
            "commission_bps": rate,
            "commission_idr": _apply_bps(base, rate),
            "status": "recorded_unpaid",
            "notes": _pick(args, "notes"),
            "recorded_by": ctx.actor,
        },
    )
    ctx.event("dealer.settlement.recorded", {"dealer_id": dealer["id"], "period": period})
    return {"settlement": settlement, "note": "payout execution requires a finance connector; nothing is paid"}


def h_dealer_stock_allocation(ctx, args):
    dealer = _need(ctx, "dealer", args["dealer_id"], "dealer_not_found")
    require(dealer.get("status") == "active", "dealer_inactive", 409)
    qty = args["qty"]
    require(type(qty) is int and 1 <= qty <= 10000, "invalid_qty")
    batch_id = _pick(args, "voucher_batch_id")
    assigned = 0
    if args["item"] == "voucher_batch":
        require(batch_id is not None, "voucher_batch_required")
        batch = _need(ctx, "voucher_batch", batch_id, "batch_not_found")
        target = min(qty, 500)
        for voucher in _bounded(ctx, "voucher", {"batch_id": batch["id"], "status": "active"}):
            if assigned >= target:
                break
            if voucher.get("dealer_id") is not None:
                continue
            ctx.update("voucher", voucher["id"], {"dealer_id": dealer["id"]})
            assigned += 1
    allocation = ctx.create(
        "stock_allocation",
        {
            "dealer_id": dealer["id"],
            "item": args["item"],
            "qty": qty,
            "voucher_batch_id": batch_id,
            "vouchers_assigned": assigned,
            "status": "recorded",
            "created_by": ctx.actor,
        },
    )
    return {"allocation": allocation, "vouchers_assigned": assigned}


def h_dealer_sales_report(ctx, args):
    dealer = _need(ctx, "dealer", args["dealer_id"], "dealer_not_found")
    period = _pick(args, "period")
    orders = _bounded(ctx, "sales_order", {"dealer_id": dealer["id"]})
    approved = [order for order in orders if order.get("status") == "approved"]
    if period is not None:
        _period(period)
        approved = [order for order in approved if str(order.get("approved_at", "")).startswith(period)]
    return {
        "dealer_id": dealer["id"],
        "period": period,
        "orders_approved": len(approved),
        "sales_idr": sum(_net_order_amount(order) for order in approved),
        "distinct_subscribers": len({order["subscriber_id"] for order in approved}),
        "bounded_scan": True,
    }


def h_dealer_performance_track(ctx, args):
    period = _pick(args, "period")
    ranking = []
    for dealer in _bounded(ctx, "dealer", {"status": "active"}):
        sales = _approved_orders_net(ctx, "dealer_id", dealer["id"], period)
        ranking.append({"dealer_id": dealer["id"], "name": dealer["name"], "sales_idr": sales})
    ranking.sort(key=lambda row: (-row["sales_idr"], row["dealer_id"]))
    for index, row in enumerate(ranking, start=1):
        row["rank"] = index
    return {"period": period, "ranking": ranking, "bounded_scan": True}


def h_dealer_termination(ctx, args):
    dealer = _need(ctx, "dealer", args["dealer_id"], "dealer_not_found")
    require(dealer.get("status") == "active", "invalid_dealer_transition", 409)
    open_settlements = _bounded(ctx, "dealer_settlement", {"dealer_id": dealer["id"], "status": "recorded_unpaid"})
    updated = ctx.update(
        "dealer",
        dealer["id"],
        {"status": "terminated", "terminated_at": ctx.now, "termination_reason": args["reason"]},
    )
    ctx.event("dealer.terminated", {"dealer_id": dealer["id"], "open_settlements": len(open_settlements)})
    return {
        "dealer": updated,
        "open_settlements_idr": sum(s["commission_idr"] for s in open_settlements),
        "note": "recorded_unpaid settlements remain receivables for the finance process",
    }


def h_partner_contract_manage(ctx, args):
    action = args["action"]
    if action == "create":
        starts = _date(args["starts_at"])
        ends = _date(args["ends_at"])
        require(starts < ends, "invalid_contract_window")
        require(type(args["revenue_share_bps"]) is int and 0 <= args["revenue_share_bps"] <= 10000,
                "invalid_share_bps")
        contract = ctx.create(
            "partner_contract",
            {
                "partner_name": args["partner_name"],
                "revenue_share_bps": args["revenue_share_bps"],
                "starts_at": args["starts_at"],
                "ends_at": args["ends_at"],
                "notes": _pick(args, "notes"),
                "status": "active",
                "created_by": ctx.actor,
            },
        )
        return {"partner_contract": contract}
    contract = _need(ctx, "partner_contract", args["contract_id"], "contract_not_found")
    require(contract.get("status") == "active", "invalid_contract_transition", 409)
    updated = ctx.update("partner_contract", contract["id"], {"status": "ended", "ended_at": ctx.now})
    return {"partner_contract": updated}


def h_partner_revenue_share(ctx, args):
    contract = _need(ctx, "partner_contract", args["contract_id"], "contract_not_found")
    require(contract.get("status") == "active", "contract_not_active", 409)
    revenue = money(args["revenue_idr"])
    share = _apply_bps(revenue, contract["revenue_share_bps"])
    return {
        "calculation_only": True,
        "contract_id": contract["id"],
        "revenue_idr": revenue,
        "revenue_share_bps": contract["revenue_share_bps"],
        "partner_share_idr": share,
        "company_retained_idr": revenue - share,
        "rounding": "integer_round_half_up",
    }


def h_partner_portal_access(ctx, args):
    contract = _need(ctx, "partner_contract", args["contract_id"], "contract_not_found")
    require(contract.get("status") == "active", "contract_not_active", 409)
    access = ctx.create(
        "partner_access",
        {
            "contract_id": contract["id"],
            "partner_name": contract["partner_name"],
            "scope": args["scope"],
            "status": "granted_local_record",
            "credentials_issued": False,
            "granted_by": ctx.actor,
        },
    )
    return {
        "partner_access": access,
        "note": "local access record only; real portal authentication is owned by the root token boundary",
    }


def h_portal_profile_view(ctx, args):
    subscriber = _subscriber(ctx, args)
    active_contract = None
    for contract in _bounded(ctx, "contract", {"subscriber_id": subscriber["id"]}):
        if contract.get("status") == "active":
            active_contract = contract
            break
    return {
        "profile": {
            "subscriber_id": subscriber["id"],
            "name": subscriber["name"],
            "status": subscriber["status"],
            "segment": subscriber.get("segment"),
            "package_id": subscriber.get("package_id"),
            "address": subscriber.get("address"),
            "created_at": subscriber["created_at"],
        },
        "active_contract": active_contract,
        "note": "identity binding is performed by root auth; no customer login exists in this handler",
    }


def h_portal_usage_view(ctx, args):
    subscriber = _subscriber(ctx, args)
    rows = _bounded(ctx, "usage_record", {"subscriber_id": subscriber["id"]})
    period = _pick(args, "period")
    if period is not None:
        _period(period)
        rows = [row for row in rows if row.get("period") == period]
    return {
        "subscriber_id": subscriber["id"],
        "period": period,
        "usage_records": rows,
        "count": len(rows),
        "note": "usage evidence is produced by network/operations systems; none is fabricated here",
        "bounded_scan": True,
    }


def h_portal_invoice_view(ctx, args):
    subscriber = _subscriber(ctx, args)
    invoices = []
    open_total = 0
    for invoice in _bounded(ctx, "invoice", {"subscriber_id": subscriber["id"]}):
        remaining, _status = _invoice_state(ctx, invoice)
        open_total += remaining
        invoices.append(
            {"invoice_id": invoice["id"], "period": invoice["period"], "amount_idr": invoice["amount_idr"],
             "paid_idr": invoice["paid_idr"], "remaining_idr": remaining, "due_at": invoice["due_at"],
             "status": invoice["status"]}
        )
    return {"subscriber_id": subscriber["id"], "invoices": invoices, "open_total_idr": open_total,
            "bounded_scan": True}


def h_portal_ticket_create(ctx, args):
    subscriber = _subscriber(ctx, args)
    ticket = ctx.create(
        "ticket",
        {
            "subscriber_id": subscriber["id"],
            "subject": args["subject"],
            "description": _pick(args, "description"),
            "severity": _pick(args, "severity", "P3"),
            "status": "open",
            "channel": "portal",
            "created_by": ctx.actor,
        },
    )
    ctx.event("portal.ticket.created", {"ticket_id": ticket["id"], "subscriber_id": subscriber["id"]})
    return {"ticket": ticket}


def h_portal_package_change(ctx, args):
    subscriber = _subscriber(ctx, args)
    _require_billable_subscriber(subscriber)
    product = _need(ctx, "product", args["new_product_id"], "product_not_found")
    _require_live_product(product)
    _period(args["effective_period"])
    previous = subscriber.get("package_id")
    require(previous != product["id"], "package_unchanged", 409)
    change = ctx.create(
        "package_change",
        {
            "subscriber_id": subscriber["id"],
            "from_package_id": previous,
            "to_package_id": product["id"],
            "effective_period": args["effective_period"],
            "status": "recorded_pending_provisioning",
            "requested_by": ctx.actor,
        },
    )
    ctx.update("subscriber", subscriber["id"], {"package_id": product["id"]})
    ctx.event(
        "portal.package.change_recorded",
        {"subscriber_id": subscriber["id"], "to_package_id": product["id"], "network_effect": "none"},
    )
    return {
        "package_change": change,
        "network_effect": "none; live reprovisioning is owned by the operations worker with real resources",
    }


def h_portal_feedback_submit(ctx, args):
    subscriber = _subscriber(ctx, args)
    feedback = ctx.create(
        "feedback",
        {
            "subscriber_id": subscriber["id"],
            "category": args["category"],
            "rating": _pick(args, "rating"),
            "text": args["text"],
            "status": "received",
            "created_by": ctx.actor,
        },
    )
    return {"feedback": feedback}


def h_portal_notification_center(ctx, args):
    action = args["action"]
    subscriber = _subscriber(ctx, args)
    if action == "queue":
        require(bool(args.get("template")), "template_required")
        notification = ctx.create(
            "notification",
            {
                "subscriber_id": subscriber["id"],
                "template": args["template"],
                "payload_ref": _pick(args, "payload_ref"),
                "status": "recorded_not_sent",
                "queued_by": ctx.actor,
            },
        )
        ctx.event("portal.notification.queued_local", {"subscriber_id": subscriber["id"], "template": args["template"]})
        return {"notification": notification,
                "note": "recorded only; delivery requires a consented messaging connector"}
    rows = _bounded(ctx, "notification", {"subscriber_id": subscriber["id"]})
    return {"subscriber_id": subscriber["id"], "notifications": rows, "count": len(rows), "bounded_scan": True}


def h_nps_score_calculate(ctx, args):
    responses = args["responses"]
    for score in responses:
        require(type(score) is int and 0 <= score <= 10, "invalid_nps_response")
    stats = _nps_stats(responses)
    return {**stats, "scale": "-100..100", "bounded_input": True}


def h_nps_trend_analyze(ctx, args):
    series = args["series"]
    trend = []
    previous_period = None
    previous_score = None
    for point in series:
        _period(point["period"])
        for score in point["responses"]:
            require(type(score) is int and 0 <= score <= 10, "invalid_nps_response")
        if previous_period is not None:
            require(point["period"] > previous_period, "periods_not_ascending")
        stats = _nps_stats(point["responses"])
        delta = None if previous_score is None else stats["nps_score"] - previous_score
        trend.append({"period": point["period"], "nps_score": stats["nps_score"], "delta": delta,
                      "total": stats["total"]})
        previous_period = point["period"]
        previous_score = stats["nps_score"]
    direction = "flat"
    if len(trend) >= 2 and trend[-1]["delta"] is not None:
        direction = "improving" if trend[-1]["delta"] > 0 else ("declining" if trend[-1]["delta"] < 0 else "flat")
    return {"trend": trend, "overall_direction": direction}


def h_nps_report_monthly(ctx, args):
    period = args["period"]
    _period(period)
    if _pick(args, "responses") is not None:
        responses = list(args["responses"])
        source = "explicit_input"
    else:
        rows = _bounded(ctx, "nps_response", {"period": period})
        responses = [row["score"] for row in rows]
        source = "nps_response_records"
    if not responses:
        return {"period": period, "source": source, "response_count": 0, "nps_score": None,
                "note": "no responses for period; responses enter via adapter or fixture records only"}
    stats = _nps_stats(responses)
    return {
        "period": period,
        "source": source,
        **stats,
        "detractor_followups_open": len(
            [row for row in _bounded(ctx, "followup_task", {"period": period}) if row.get("status") == "open"]
        ),
        "bounded_scan": True,
    }


def h_nps_feedback_analyze(ctx, args):
    text = args["text"]
    sentiment, positives, negatives = _sentiment(text)
    return {
        "heuristic": True,
        "sentiment": sentiment,
        "positive_keywords": positives,
        "negative_keywords": negatives,
        "note": "fixed bilingual keyword heuristic, not an ML sentiment model",
    }


def h_nps_detractor_followup(ctx, args):
    response_id = _pick(args, "nps_response_id")
    subscriber_id = _pick(args, "subscriber_id")
    require(response_id is not None or subscriber_id is not None, "followup_target_required")
    if response_id is not None:
        response = _need(ctx, "nps_response", response_id, "response_not_found")
        require(response["score"] <= 6, "not_a_detractor_response", 409)
        subscriber_id = response.get("subscriber_id")
    elif subscriber_id is not None:
        _subscriber(ctx, args)
    task = ctx.create(
        "followup_task",
        {
            "subscriber_id": subscriber_id,
            "nps_response_id": response_id,
            "period": _pick(args, "period"),
            "reason": args["reason"],
            "priority": _pick(args, "priority", "high"),
            "status": "open",
            "created_by": ctx.actor,
        },
    )
    return {"followup_task": task}


def h_nps_action_plan(ctx, args):
    plan = ctx.create(
        "nps_action_plan",
        {
            "area": args["area"],
            "actions": args["actions"],
            "owner_ref": _pick(args, "owner_ref"),
            "period": _pick(args, "period"),
            "status": "draft",
            "created_by": ctx.actor,
        },
    )
    return {"nps_action_plan": plan}


def h_nps_benchmark_industry(ctx, args):
    ours = args["our_nps"]
    benchmark = args["industry_benchmark"]
    require(type(ours) is int and -100 <= ours <= 100, "invalid_nps_score")
    require(type(benchmark) is int and -100 <= benchmark <= 100, "invalid_nps_score")
    gap = ours - benchmark
    if gap >= 10:
        verdict = "leading"
    elif gap >= 0:
        verdict = "at_benchmark"
    elif gap >= -10:
        verdict = "below"
    else:
        verdict = "lagging"
    return {
        "our_nps": ours,
        "industry_benchmark": benchmark,
        "sector": _pick(args, "sector"),
        "gap": gap,
        "verdict": verdict,
        "note": "benchmark value must be supplied by the caller; none is hardcoded",
    }


def _s(properties, required):
    return _schema(properties, required)


SCHEMA_MAP = {
    "bill.invoice.generate": _s(
        {"subscriber_id": _sid(), "product_id": _sid(), "period": _PERIOD, "due_at": _TS}, 
        ["subscriber_id", "product_id", "period", "due_at"],
    ),
    "bill.cycle.run": _s(
        {"period": _PERIOD, "due_at": _TS, "product_id": _sid(), "max_invoices": _int(1, LIST_CAP)},
        ["period", "due_at"],
    ),
    "bill.payment.record": _s(
        {
            "subscriber_id": _sid(),
            "amount_idr": _money_pos(),
            "provider_reference": _ref(),
            "channel": _enum(("bank_transfer", "virtual_account", "ewallet", "cash", "dealer_collection", "other")),
            "evidence": _s(
                {"confirmed_at": _TS, "bank_ref": _ref(), "note": _text(1, 200)}, []
            ),
        },
        ["subscriber_id", "amount_idr", "provider_reference", "channel"],
    ),
    "bill.payment.match": _s(
        {"payment_id": _sid(), "invoice_ids": _arr(_sid(), 1, 100)},
        ["payment_id"],
    ),
    "bill.arrears.calculate": _s(
        {"subscriber_id": _sid(), "as_of": _TS},
        ["subscriber_id"],
    ),
    "bill.dunning.run": _s(
        {
            "subscriber_id": _sid(),
            "level": _enum(("reminder", "warning", "final_notice", "suspend_request")),
            "invoice_id": _sid(),
        },
        ["subscriber_id", "level"],
    ),
    "bill.late.fee": _s(
        {"invoice_id": _sid(), "rate_bps": _int(0, 100000), "grace_days": _int(0, 365), "as_of": _TS},
        ["invoice_id", "rate_bps"],
    ),
    "bill.discount.apply": _s(
        {"invoice_id": _sid(), "amount_idr": _money_pos(), "reason": _text(3, 200), "approved_by": _ref()},
        ["invoice_id", "amount_idr", "reason"],
    ),
    "bill.prorate.calculate": _s(
        {
            "period": _PERIOD,
            "price_idr": _money(),
            "product_id": _sid(),
            "active_from": _DATE,
            "active_to": _DATE,
        },
        ["period", "active_from"],
    ),
    "bill.deposit.manage": _s(
        {
            "subscriber_id": _sid(),
            "action": _enum(("collect", "release")),
            "amount_idr": _money_pos(),
            "reason": _text(3, 200),
        },
        ["subscriber_id", "action", "amount_idr", "reason"],
    ),
    "bill.refund.process": _s(
        {"payment_id": _sid(), "invoice_id": _sid(), "amount_idr": _money_pos(), "reason": _text(3, 300)},
        ["amount_idr", "reason"],
    ),
    "bill.adjustment.create": _s(
        {
            "subscriber_id": _sid(),
            "invoice_id": _sid(),
            "direction": _enum(("credit", "debit")),
            "amount_idr": _money_pos(),
            "reason": _text(3, 300),
            "category": _enum(("goodwill", "compensation", "correction", "other")),
        },
        ["subscriber_id", "direction", "amount_idr", "reason"],
    ),
    "bill.statement.generate": _s(
        {"subscriber_id": _sid(), "period": _PERIOD},
        ["subscriber_id", "period"],
    ),
    "bill.tax.calculate": _s(
        {"base_idr": _money(), "rate_bps": _int(0, 1000000), "policy_id": _sid(), "effective_date": _DATE},
        ["base_idr", "rate_bps", "policy_id", "effective_date"],
    ),
    "prepaid.balance.check": _s({"subscriber_id": _sid()}, ["subscriber_id"]),
    "prepaid.topup": _s(
        {
            "subscriber_id": _sid(),
            "amount_idr": _money_pos(),
            "funding": _s(
                {
                    "channel": _enum(("bank_transfer", "virtual_account", "ewallet", "voucher", "dealer")),
                    "reference": _ref(),
                    "confirmed_at": _TS,
                    "adapter_confirmation_id": _ref(),
                    "verified_by_adapter": {"type": "boolean"},
                },
                ["channel", "reference", "confirmed_at", "adapter_confirmation_id", "verified_by_adapter"],
            ),
        },
        ["subscriber_id", "amount_idr", "funding"],
    ),
    "prepaid.deduct": _s(
        {
            "subscriber_id": _sid(),
            "amount_idr": _money_pos(),
            "reason": _enum(("usage", "fee", "adjustment", "other")),
            "usage_ref": _ref(),
        },
        ["subscriber_id", "amount_idr", "reason"],
    ),
    "voucher.generate": _s(
        {"count": _int(1, 200), "amount_idr": _money_pos(), "expires_at": _TS, "batch_name": _text(1, 100)},
        ["count", "amount_idr"],
    ),
    "voucher.distribute": _s(
        {"batch_id": _sid(), "dealer_id": _sid(), "max_count": _int(1, 500)},
        ["batch_id", "dealer_id", "max_count"],
    ),
    "voucher.redeem": _s(
        {"code": _text(8, 64), "subscriber_id": _sid()},
        ["code", "subscriber_id"],
    ),
    "voucher.validate": _s({"code": _text(8, 64)}, ["code"]),
    "voucher.expire.handle": _s({"batch_id": _sid()}, []),
    "voucher.audit.report": _s(
        {"batch_id": _sid(), "suspicious_threshold": _int(1, 100)},
        ["batch_id"],
    ),
    "product.create": _s(
        {
            "name": _text(1, 100),
            "price_idr": _money(),
            "speed_mbps": _int(1, 10000000),
            "segment_target": _enum(("residential", "business", "enterprise", "wholesale", "all")),
            "description": _text(1, 500),
        },
        ["name", "price_idr"],
    ),
    "product.update": _s(
        {
            "product_id": _sid(),
            "name": _text(1, 100),
            "description": _text(1, 500),
            "speed_mbps": _int(1, 10000000),
            "segment_target": _enum(("residential", "business", "enterprise", "wholesale", "all")),
        },
        ["product_id"],
    ),
    "product.pricing.set": _s(
        {"product_id": _sid(), "price_idr": _money(), "reason": _text(3, 200), "effective_from": _TS},
        ["product_id", "price_idr", "reason"],
    ),
    "product.bundling": _s(
        {
            "name": _text(1, 100),
            "component_product_ids": _arr(_sid(), 2, 10),
            "price_idr": _money(),
            "description": _text(1, 500),
        },
        ["name", "component_product_ids", "price_idr"],
    ),
    "product.promo.create": _s(
        {
            "name": _text(1, 100),
            "product_id": _sid(),
            "discount_bps": _int(1, 10000),
            "discount_idr": _money_pos(),
            "valid_from": _DATE,
            "valid_to": _DATE,
            "max_redemptions": _int(1, 100000),
        },
        ["name", "valid_from", "valid_to"],
    ),
    "product.promo.validate": _s(
        {"promo_id": _sid(), "subscriber_id": _sid()},
        ["promo_id", "subscriber_id"],
    ),
    "product.lifecycle.manage": _s(
        {"product_id": _sid(), "action": _enum(("retire", "reactivate"))},
        ["product_id", "action"],
    ),
    "product.eligibility.check": _s(
        {"subscriber_id": _sid(), "product_id": _sid()},
        ["subscriber_id", "product_id"],
    ),
    "product.feature.attaching": _s(
        {"product_id": _sid(), "feature_name": _text(1, 80), "price_idr": _money()},
        ["product_id", "feature_name", "price_idr"],
    ),
    "product.compatibility.check": _s(
        {"product_id_a": _sid(), "product_id_b": _sid()},
        ["product_id_a", "product_id_b"],
    ),
    "sub.create": _s(
        {
            "name": _text(1, 100),
            "segment": _enum(SEGMENTS),
            "contact_email": _ref(200),
            "contact_phone": _phone(),
            "address": _text(5, 300),
            "external_customer_id": _ref(),
            "package_id": _sid(),
        },
        ["name"],
    ),
    "sub.profile.update": _s(
        {
            "subscriber_id": _sid(),
            "name": _text(1, 100),
            "contact_email": _ref(200),
            "contact_phone": _phone(),
            "address": _text(5, 300),
            "notes": _text(1, 300),
        },
        ["subscriber_id"],
    ),
    "sub.kyc.verify": _s(
        {
            "subscriber_id": _sid(),
            "evidence": _s(
                {
                    "doc_type": _enum(("ktp", "passport", "drivers_license", "other")),
                    "doc_ref": _ref(),
                    "selfie_ref": _ref(),
                },
                ["doc_type", "doc_ref"],
            ),
        },
        ["subscriber_id", "evidence"],
    ),
    "sub.status.manage": _s(
        {
            "subscriber_id": _sid(),
            "new_status": _enum(("active", "suspended", "terminated")),
            "local_activation_confirmed": {"type": "boolean"},
        },
        ["subscriber_id", "new_status"],
    ),
    "sub.contact.manage": _s(
        {
            "subscriber_id": _sid(),
            "action": _enum(("add", "update", "remove", "set_primary")),
            "contact_id": _sid(),
            "name": _text(1, 100),
            "phone": _phone(),
            "email": _ref(200),
            "role": _enum(("primary", "billing", "technical", "other")),
            "is_primary": {"type": "boolean"},
        },
        ["subscriber_id", "action"],
    ),
    "sub.address.verify": _s(
        {"subscriber_id": _sid(), "address": _text(5, 300), "evidence_ref": _ref()},
        ["subscriber_id", "address"],
    ),
    "sub.contract.manage": _s(
        {
            "subscriber_id": _sid(),
            "action": _enum(("create", "end")),
            "months": _int(1, 36),
            "start_at": _DATE,
            "penalty_idr": _money(),
            "contract_id": _sid(),
        },
        ["subscriber_id", "action"],
    ),
    "sub.document.store": _s(
        {
            "subscriber_id": _sid(),
            "doc_type": _enum(("ktp", "application_form", "contract", "other")),
            "doc_ref": _ref(),
            "content_sha256": _sha256(),
            "notes": _text(1, 300),
        },
        ["subscriber_id", "doc_type", "doc_ref"],
    ),
    "sub.segmentation": _s(
        {"subscriber_id": _sid(), "segment": _enum(SEGMENTS)},
        ["subscriber_id", "segment"],
    ),
    "sub.lifetime.value": _s(
        {"subscriber_id": _sid(), "expected_months": _int(1, 120)},
        ["subscriber_id", "expected_months"],
    ),
    "sub.churn.predict": _s({"subscriber_id": _sid()}, ["subscriber_id"]),
    "sub.merge.duplicate": _s(
        {"survivor_id": _sid(), "duplicate_id": _sid(), "reason": _text(3, 300)},
        ["survivor_id", "duplicate_id", "reason"],
    ),
    "sales.lead.capture": _s(
        {
            "source": _enum(("walk_in", "web", "phone", "partner", "campaign")),
            "contact_name": _text(1, 100),
            "contact_ref": _ref(),
            "campaign_id": _sid(),
            "product_id": _sid(),
            "notes": _text(1, 300),
        },
        ["source", "contact_name", "contact_ref"],
    ),
    "sales.lead.qualify": _s(
        {
            "lead_id": _sid(),
            "decision": _enum(("qualified", "disqualified")),
            "reason": _text(3, 200),
            "coverage_evidence_ref": _ref(),
        },
        ["lead_id", "decision", "reason"],
    ),
    "sales.quote.generate": _s(
        {
            "lead_id": _sid(),
            "subscriber_id": _sid(),
            "product_id": _sid(),
            "install_fee_idr": _money(),
            "discount_idr": _money(),
            "valid_days": _int(1, 90),
        },
        ["product_id"],
    ),
    "sales.order.create": _s(
        {
            "subscriber_id": _sid(),
            "product_id": _sid(),
            "quote_id": _sid(),
            "sales_id": _ref(),
            "dealer_id": _sid(),
        },
        ["subscriber_id", "product_id"],
    ),
    "sales.order.approve": _s({"order_id": _sid()}, ["order_id"]),
    "sales.commission.calc": _s(
        {
            "period": _PERIOD,
            "earner_type": _enum(("sales", "dealer")),
            "earner_id": _ref(),
            "rate_bps": _int(0, 10000),
        },
        ["period", "earner_type", "earner_id"],
    ),
    "sales.target.track": _s(
        {"sales_id": _ref(), "period": _PERIOD, "target_idr": _money_pos()},
        ["sales_id", "period", "target_idr"],
    ),
    "sales.funnel.analyze": _s({"period": _PERIOD}, []),
    "sales.campaign.track": _s(
        {
            "action": _enum(("create", "report")),
            "name": _text(1, 100),
            "channel": _enum(("social", "broadcast", "event", "partner", "other")),
            "starts_at": _DATE,
            "ends_at": _DATE,
            "budget_idr": _money(),
            "campaign_id": _sid(),
        },
        ["action"],
    ),
    "sales.referral.manage": _s(
        {
            "action": _enum(("record", "approve_reward")),
            "referrer_subscriber_id": _sid(),
            "referred_contact_ref": _ref(),
            "notes": _text(1, 300),
            "referral_id": _sid(),
            "reward_idr": _money_pos(),
        },
        ["action"],
    ),
    "dealer.register": _s(
        {"name": _text(1, 100), "region": _text(1, 80), "commission_bps": _int(0, 5000)},
        ["name", "region"],
    ),
    "dealer.commission": _s(
        {"dealer_id": _sid(), "period": _PERIOD},
        ["dealer_id", "period"],
    ),
    "dealer.settlement": _s(
        {"dealer_id": _sid(), "period": _PERIOD, "notes": _text(1, 300)},
        ["dealer_id", "period"],
    ),
    "dealer.stock.allocation": _s(
        {
            "dealer_id": _sid(),
            "item": _enum(("voucher_batch", "sim", "ont")),
            "qty": _int(1, 10000),
            "voucher_batch_id": _sid(),
        },
        ["dealer_id", "item", "qty"],
    ),
    "dealer.sales.report": _s(
        {"dealer_id": _sid(), "period": _PERIOD},
        ["dealer_id"],
    ),
    "dealer.performance.track": _s({"period": _PERIOD}, []),
    "dealer.termination": _s(
        {"dealer_id": _sid(), "reason": _text(3, 300)},
        ["dealer_id", "reason"],
    ),
    "partner.contract.manage": _s(
        {
            "action": _enum(("create", "end")),
            "partner_name": _text(1, 100),
            "revenue_share_bps": _int(0, 10000),
            "starts_at": _DATE,
            "ends_at": _DATE,
            "notes": _text(1, 300),
            "contract_id": _sid(),
        },
        ["action"],
    ),
    "partner.revenue.share": _s(
        {"contract_id": _sid(), "revenue_idr": _money()},
        ["contract_id", "revenue_idr"],
    ),
    "partner.portal.access": _s(
        {
            "contract_id": _sid(),
            "scope": _arr(_enum(("orders", "statements", "voucher_stock")), 1, 10),
        },
        ["contract_id", "scope"],
    ),
    "portal.profile.view": _s({"subscriber_id": _sid()}, ["subscriber_id"]),
    "portal.usage.view": _s(
        {"subscriber_id": _sid(), "period": _PERIOD},
        ["subscriber_id"],
    ),
    "portal.invoice.view": _s({"subscriber_id": _sid()}, ["subscriber_id"]),
    "portal.ticket.create": _s(
        {
            "subscriber_id": _sid(),
            "subject": _text(5, 200),
            "description": _text(1, 1000),
            "severity": _enum(("P1", "P2", "P3")),
        },
        ["subscriber_id", "subject"],
    ),
    "portal.package.change": _s(
        {"subscriber_id": _sid(), "new_product_id": _sid(), "effective_period": _PERIOD},
        ["subscriber_id", "new_product_id", "effective_period"],
    ),
    "portal.feedback.submit": _s(
        {
            "subscriber_id": _sid(),
            "category": _enum(("service", "billing", "installation", "other")),
            "rating": _int(1, 5),
            "text": _text(1, 1000),
        },
        ["subscriber_id", "category", "text"],
    ),
    "portal.notification.center": _s(
        {
            "action": _enum(("queue", "list")),
            "subscriber_id": _sid(),
            "template": _enum(("invoice_ready", "payment_received", "outage_notice", "package_change", "promo", "other")),
            "payload_ref": _ref(),
        },
        ["action", "subscriber_id"],
    ),
    "nps.score.calculate": _s(
        {"responses": _arr(_int(0, 10), 1, 10000)},
        ["responses"],
    ),
    "nps.trend.analyze": _s(
        {
            "series": _arr(
                _s({"period": _PERIOD, "responses": _arr(_int(0, 10), 1, 10000)}, ["period", "responses"]), 1, 60
            )
        },
        ["series"],
    ),
    "nps.report.monthly": _s(
        {"period": _PERIOD, "responses": _arr(_int(0, 10), 1, 10000)},
        ["period"],
    ),
    "nps.feedback.analyze": _s({"text": _text(1, 1000)}, ["text"]),
    "nps.detractor.followup": _s(
        {
            "subscriber_id": _sid(),
            "nps_response_id": _sid(),
            "reason": _text(3, 300),
            "priority": _enum(("high", "medium", "low")),
            "period": _PERIOD,
        },
        ["reason"],
    ),
    "nps.action.plan": _s(
        {
            "area": _enum(("billing", "network", "support", "onboarding", "other")),
            "actions": _arr(_text(1, 200), 1, 20),
            "owner_ref": _ref(),
            "period": _PERIOD,
        },
        ["area", "actions"],
    ),
    "nps.benchmark.industry": _s(
        {"our_nps": _int(-100, 100), "industry_benchmark": _int(-100, 100), "sector": _text(1, 80)},
        ["our_nps", "industry_benchmark"],
    ),
}

HANDLERS = {
    "bill.invoice.generate": h_bill_invoice_generate,
    "bill.cycle.run": h_bill_cycle_run,
    "bill.payment.record": h_bill_payment_record,
    "bill.payment.match": h_bill_payment_match,
    "bill.arrears.calculate": h_bill_arrears_calculate,
    "bill.dunning.run": h_bill_dunning_run,
    "bill.late.fee": h_bill_late_fee,
    "bill.discount.apply": h_bill_discount_apply,
    "bill.prorate.calculate": h_bill_prorate_calculate,
    "bill.deposit.manage": h_bill_deposit_manage,
    "bill.refund.process": h_bill_refund_process,
    "bill.adjustment.create": h_bill_adjustment_create,
    "bill.statement.generate": h_bill_statement_generate,
    "bill.tax.calculate": h_bill_tax_calculate,
    "prepaid.balance.check": h_prepaid_balance_check,
    "prepaid.topup": h_prepaid_topup,
    "prepaid.deduct": h_prepaid_deduct,
    "voucher.generate": h_voucher_generate,
    "voucher.distribute": h_voucher_distribute,
    "voucher.redeem": h_voucher_redeem,
    "voucher.validate": h_voucher_validate,
    "voucher.expire.handle": h_voucher_expire_handle,
    "voucher.audit.report": h_voucher_audit_report,
    "product.create": h_product_create,
    "product.update": h_product_update,
    "product.pricing.set": h_product_pricing_set,
    "product.bundling": h_product_bundling,
    "product.promo.create": h_product_promo_create,
    "product.promo.validate": h_product_promo_validate,
    "product.lifecycle.manage": h_product_lifecycle_manage,
    "product.eligibility.check": h_product_eligibility_check,
    "product.feature.attaching": h_product_feature_attaching,
    "product.compatibility.check": h_product_compatibility_check,
    "sub.create": h_sub_create,
    "sub.profile.update": h_sub_profile_update,
    "sub.kyc.verify": h_sub_kyc_verify,
    "sub.status.manage": h_sub_status_manage,
    "sub.contact.manage": h_sub_contact_manage,
    "sub.address.verify": h_sub_address_verify,
    "sub.contract.manage": h_sub_contract_manage,
    "sub.document.store": h_sub_document_store,
    "sub.segmentation": h_sub_segmentation,
    "sub.lifetime.value": h_sub_lifetime_value,
    "sub.churn.predict": h_sub_churn_predict,
    "sub.merge.duplicate": h_sub_merge_duplicate,
    "sales.lead.capture": h_sales_lead_capture,
    "sales.lead.qualify": h_sales_lead_qualify,
    "sales.quote.generate": h_sales_quote_generate,
    "sales.order.create": h_sales_order_create,
    "sales.order.approve": h_sales_order_approve,
    "sales.commission.calc": h_sales_commission_calc,
    "sales.target.track": h_sales_target_track,
    "sales.funnel.analyze": h_sales_funnel_analyze,
    "sales.campaign.track": h_sales_campaign_track,
    "sales.referral.manage": h_sales_referral_manage,
    "dealer.register": h_dealer_register,
    "dealer.commission": h_dealer_commission,
    "dealer.settlement": h_dealer_settlement,
    "dealer.stock.allocation": h_dealer_stock_allocation,
    "dealer.sales.report": h_dealer_sales_report,
    "dealer.performance.track": h_dealer_performance_track,
    "dealer.termination": h_dealer_termination,
    "partner.contract.manage": h_partner_contract_manage,
    "partner.revenue.share": h_partner_revenue_share,
    "partner.portal.access": h_partner_portal_access,
    "portal.profile.view": h_portal_profile_view,
    "portal.usage.view": h_portal_usage_view,
    "portal.invoice.view": h_portal_invoice_view,
    "portal.ticket.create": h_portal_ticket_create,
    "portal.package.change": h_portal_package_change,
    "portal.feedback.submit": h_portal_feedback_submit,
    "portal.notification.center": h_portal_notification_center,
    "nps.score.calculate": h_nps_score_calculate,
    "nps.trend.analyze": h_nps_trend_analyze,
    "nps.report.monthly": h_nps_report_monthly,
    "nps.feedback.analyze": h_nps_feedback_analyze,
    "nps.detractor.followup": h_nps_detractor_followup,
    "nps.action.plan": h_nps_action_plan,
    "nps.benchmark.industry": h_nps_benchmark_industry,
}

SCHEMAS = SCHEMA_MAP

MUTATIONS = {
    "bill.invoice.generate",
    "bill.cycle.run",
    "bill.payment.record",
    "bill.payment.match",
    "bill.dunning.run",
    "bill.discount.apply",
    "bill.deposit.manage",
    "bill.refund.process",
    "bill.adjustment.create",
    "prepaid.topup",
    "prepaid.deduct",
    "voucher.generate",
    "voucher.distribute",
    "voucher.redeem",
    "voucher.expire.handle",
    "product.create",
    "product.update",
    "product.pricing.set",
    "product.bundling",
    "product.promo.create",
    "product.lifecycle.manage",
    "product.feature.attaching",
    "sub.create",
    "sub.profile.update",
    "sub.kyc.verify",
    "sub.status.manage",
    "sub.contact.manage",
    "sub.address.verify",
    "sub.contract.manage",
    "sub.document.store",
    "sub.segmentation",
    "sub.merge.duplicate",
    "sales.lead.capture",
    "sales.lead.qualify",
    "sales.quote.generate",
    "sales.order.create",
    "sales.order.approve",
    "sales.campaign.track",
    "sales.referral.manage",
    "dealer.register",
    "dealer.settlement",
    "dealer.stock.allocation",
    "dealer.termination",
    "partner.contract.manage",
    "partner.portal.access",
    "portal.ticket.create",
    "portal.package.change",
    "portal.feedback.submit",
    "portal.notification.center",
    "nps.detractor.followup",
    "nps.action.plan",
}

LIMITATIONS = {
    "bill.invoice.send": "NOT IMPLEMENTED: outbound invoice delivery needs a consented messaging connector (email/WA/SMS). Nothing is sent.",
    "prepaid.expire.notify": "NOT IMPLEMENTED: expiry notification needs a consented messaging connector; ledger v1 has no balance expiry either.",
    "portal.customer.login": "NOT IMPLEMENTED: authentication belongs to the root service token boundary; no credentials are checked or issued.",
    "portal.payment.online": "NOT IMPLEMENTED: online payment needs a verified payment connector adapter; no payment is initiated.",
    "portal.speedtest": "NOT IMPLEMENTED: real speed measurement needs live network access; no synthetic results are produced.",
    "nps.survey.send": "NOT IMPLEMENTED: survey distribution needs a consented messaging connector; responses enter via adapter or fixture records.",
    "bill.cycle.run": "Bounded analytics: scans up to the ctx.list cap (1000) of products/subscribers/invoices per run; use max_invoices for explicit caps.",
    "bill.arrears.calculate": "Bounded analytics over the ctx.list cap (1000); adjustments are summed per invoice from records.",
    "bill.statement.generate": "Bounded analytics over the ctx.list cap (1000); payment attribution uses record timestamps.",
    "bill.late.fee": "Calculation-only: flat per-day bps on remaining balance, capped at the remaining amount; nothing is written.",
    "bill.prorate.calculate": "Calculation-only: calendar-day proration with integer ROUND_HALF_UP; no invoice is written.",
    "bill.tax.calculate": "Calculation-only with caller-supplied policy_id/rate_bps/effective date; no statutory rate is hardcoded and nothing is filed.",
    "bill.dunning.run": "Records dunning steps; suspend_request never changes subscriber status or network state.",
    "bill.refund.process": "Records refund requests only; execution requires a verified finance/payment connector.",
    "prepaid.topup": "Disabled at registry: caller-supplied funding flags cannot prove a payment. Requires an authenticated verified payment connector.",
    "voucher.generate": "Codes are random secrets shown once and stored only as SHA-256 hashes; max 200 per batch per call.",
    "voucher.redeem": "Exactly-once via CAS expected_version (version_conflict on race); credits local prepaid ledger only.",
    "voucher.audit.report": "Bounded analytics over the ctx.list cap (1000) per batch; suspicious = same redeemer at or above threshold.",
    "sub.kyc.verify": "Records KYC evidence and returns pending_manual_verification only; never marks verified. Needs an authorized identity connector.",
    "sub.status.manage": "Local record transitions only; live activation/suspension/termination needs the verified network connector owned by root.",
    "sub.address.verify": "Records the request only; coverage determination belongs to the operations/GIS worker with real evidence.",
    "sub.churn.predict": "Heuristic rule score (arrears, payment recency, open tickets, suspension) 0-100; not a validated ML model.",
    "sub.lifetime.value": "Heuristic projection of observed paid ARPU over requested months; not a financial forecast.",
    "sub.merge.duplicate": "Disabled at registry: distinct valid subscription accounts must never be merged; full identity/reference validation is required before any future enablement.",
    "sales.lead.qualify": "Records a human qualification decision; coverage truthfulness depends on operations evidence.",
    "sales.commission.calc": "Calculation-only over paid invoices with unique approved-order attribution and invoice period; unpaid orders earn zero. No payout is executed.",
    "sales.target.track": "Bounded analytics over the ctx.list cap (1000).",
    "sales.funnel.analyze": "Bounded analytics over the ctx.list cap (1000).",
    "sales.campaign.track": "Bounded analytics over the ctx.list cap (1000); attribution follows lead-quote-order references.",
    "sales.referral.manage": "Reward approval is a local record; payout requires a finance connector.",
    "dealer.commission": "Calculation-only over the ctx.list cap (1000); no payout is executed.",
    "dealer.settlement": "Records settlements as recorded_unpaid; payout requires a finance connector.",
    "dealer.sales.report": "Bounded analytics over the ctx.list cap (1000).",
    "dealer.performance.track": "Bounded analytics over the ctx.list cap (1000); deterministic tie-break by dealer id.",
    "partner.revenue.share": "Calculation-only integer split; no transfer is executed.",
    "partner.portal.access": "Local access record only; real portal authentication is owned by the root token boundary and no credentials are issued.",
    "portal.profile.view": "Assumes root has bound the authenticated actor to the subscriber; no customer login here.",
    "portal.usage.view": "Reads usage_record evidence produced by operations/adapters; if none exists it returns empty without fabricating data.",
    "portal.package.change": "Records the change and updates the local package reference; live reprovisioning is owned by operations.",
    "portal.notification.center": "Notifications are recorded_not_sent; delivery requires a consented messaging connector.",
    "nps.score.calculate": "Pure calculation over supplied responses (0..10); NPS range -100..100; responses are not stored.",
    "nps.report.monthly": "Aggregates explicit responses or nps_response records (adapters/fixtures); survey distribution is not implemented.",
    "nps.feedback.analyze": "Fixed bilingual keyword heuristic; not an ML sentiment model.",
    "nps.benchmark.industry": "Compares against a caller-supplied benchmark; no industry figure is hardcoded.",
}

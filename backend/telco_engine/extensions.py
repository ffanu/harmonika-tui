"""Harmonika telco EXTENSIONS domain module (worker-owned addendum).

Local operations intentionally left absent by operations.py/planning.py/
analysis.py/commercial.py, implemented here strictly as tenant-local,
deterministic functionality:

    netinv.fiber.route            shortest path over EXISTING network_link
                                  records; never promises coverage
    netinv.splitter.management    local bounded splitter ratio/capacity
                                  inventory (register/allocate/release/
                                  inspect)
    netinv.audit.physical         discrepancy between submitted physical
                                  audit evidence and local device records
    noc.monitor.cpu               CPU/RAM evaluation of operator-supplied
                                  samples (source+timestamp evidence; no
                                  fabricated polls)
    noc.map.network               local topology map from device +
                                  network_link records (not live state)
    noc.shift.handover            local structured shift handover record
    noc.incident.postmortem       post-mortem document from a locally
                                  recorded resolved ticket
    field.quality.score           supplied checklist evidence scored in
                                  integer basis points
    field.schedule.optimize       deterministic non-overlap technician
                                  schedule proposal; no booking
    qos.threshold                 local QoS threshold policy profile
    qos.benchmark                 compare supplied measurements against
                                  supplied benchmark targets
    fraud.case.investigate        record a fraud case review; NEVER a
                                  guilty verdict
    fraud.blacklist.subscriber    local blacklist REVIEW REQUEST only;
                                  NEVER an actual blacklist
    revass.discount.abuse         supplied discounts vs supplied policy
    revass.report.monthly         supplied reconciliation evidence summary
    revass.recovery.action        local approval REQUEST only; no money
                                  movement
    workflow.sla.attach           bind an EXISTING local SLA to an
                                  EXISTING local workflow template or
                                  instance (FK-checked)

Hard boundaries (CONTRACT.txt): stdlib Python 3.9, deterministic, no
filesystem/network/secret access, no live device/payment/KYC actions, no
hardcoded tax or legal rates, IDR money as strict integers only. Every
mutating ID is listed in MUTATIONS; read-only IDs are not. All ctx.list
aggregations are bounded to 1000 records and flagged as such in results.
"""

import datetime as _dt
import re as _re

from .common import DomainError, require, identifier, money, parse_time

_PERIOD_RE = _re.compile(r"\d{4}-(0[1-9]|1[0-2])")

LIST_CAP = 1000
MAX_AGE_LIMIT_S = 30 * 24 * 3600

_ID = {"type": "string", "minLength": 1, "maxLength": 100, "pattern": r"[A-Za-z0-9_-]{1,100}"}
_REF = {"type": "string", "minLength": 1, "maxLength": 100, "pattern": r"[A-Za-z0-9][A-Za-z0-9._@:+-]{0,99}"}
_MONEY = {"type": "integer", "minimum": 0, "maximum": 10 ** 15}
_PERIOD = {"type": "string", "minLength": 7, "maxLength": 7, "pattern": r"\d{4}-(0[1-9]|1[0-2])"}
_ISO = {"type": "string", "minLength": 1, "maxLength": 40}


def _sch(props, required):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(required),
        "properties": props,
    }


def _require_int(value, code="invalid_argument", minimum=None, maximum=None):
    require(type(value) is int and not isinstance(value, bool), code)
    if minimum is not None:
        require(value >= minimum, code)
    if maximum is not None:
        require(value <= maximum, code)
    return value


def _text(value, max_len, code="invalid_argument", min_len=1):
    require(isinstance(value, str) and min_len <= len(value) <= max_len, code)
    require(all(ord(ch) >= 32 for ch in value), code)
    return value


def _pick(args, key, allowed, code="invalid_argument"):
    value = args.get(key)
    require(value in allowed, code)
    return value


def _opt_id(args, key):
    value = args.get(key)
    if value is None:
        return None
    return identifier(value)


def _req_id(args, key):
    value = args.get(key)
    require(value is not None, "invalid_argument")
    return identifier(value)


def _id_list(args, key, max_items):
    value = args.get(key)
    if value is None:
        return []
    require(isinstance(value, list) and 1 <= len(value) <= max_items, "invalid_argument")
    return [identifier(item) for item in value]


def _iso_past(ctx, args, key, required=False):
    raw = args.get(key)
    if raw is None:
        require(not required, "invalid_argument")
        return None
    require(isinstance(raw, str), "invalid_argument")
    parsed = parse_time(raw)
    require(parsed <= parse_time(ctx.now), "timestamp_in_future")
    return raw


def _get(ctx, kind, record_id, code="not_found"):
    require(isinstance(record_id, str) and record_id, "invalid_argument")
    record = ctx.get(kind, record_id)
    require(record is not None, code, 404)
    return record


def _bounded(limit=LIST_CAP):
    return {"bounded": True, "limit": limit}


def _iso(dt_utc):
    return dt_utc.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _evidence_samples(ctx, raw, max_age_s, value_fields):
    """Normalize operator-supplied samples: strict fields, dedupe, freshness.

    Evidence with a future timestamp or older than max_age_s is never used;
    duplicates (same source+observed_at) are counted and ignored.
    """
    require(isinstance(raw, list) and 1 <= len(raw) <= LIST_CAP, "insufficient_evidence")
    require(type(max_age_s) is int and 1 <= max_age_s <= MAX_AGE_LIMIT_S, "invalid_argument")
    now = parse_time(ctx.now)
    fresh = []
    stale = duplicates = future = 0
    seen = set()
    for item in raw:
        require(isinstance(item, dict), "invalid_argument")
        source = identifier(item.get("source"))
        observed_raw = item.get("observed_at")
        require(isinstance(observed_raw, str), "invalid_timestamp")
        observed = parse_time(observed_raw)
        key = (source, observed)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        age = (now - observed).total_seconds()
        if age < 0:
            future += 1
            continue
        if age > max_age_s:
            stale += 1
            continue
        entry = {"source": source, "observed_at": observed_raw, "age_s": age, "_key": (observed, source)}
        for field, (lo, hi) in value_fields.items():
            value = item.get(field)
            require(type(value) is int and not isinstance(value, bool) and lo <= value <= hi, "invalid_argument")
            entry[field] = value
        fresh.append(entry)
    fresh.sort(key=lambda e: e["_key"])
    for entry in fresh:
        entry.pop("_key")
    return {"fresh": fresh, "stale": stale, "duplicates": duplicates, "future": future, "total": len(raw)}


# ---------------------------------------------------------------------------
# netinv.fiber.route
# ---------------------------------------------------------------------------


def _dijkstra(adjacency, start, goal):
    """Deterministic Dijkstra; tie-break on (distance, link_id, node)."""
    best = {start: (0, "")}
    prev = {}
    settled = set()
    while True:
        current = None
        for node, (dist, _) in best.items():
            if node in settled:
                continue
            if current is None or (dist, node) < (best[current][0], current):
                current = node
        if current is None:
            break
        settled.add(current)
        if current == goal:
            break
        for neighbor, link_id, weight in adjacency.get(current, ()):
            if neighbor in settled:
                continue
            candidate = (best[current][0] + weight, link_id)
            if neighbor not in best or candidate < best[neighbor]:
                best[neighbor] = candidate
                prev[neighbor] = (current, link_id)
    if goal not in best:
        return None
    path = [goal]
    link_ids = []
    cursor = goal
    while cursor != start:
        parent, link_id = prev[cursor]
        link_ids.append(link_id)
        path.append(parent)
        cursor = parent
    path.reverse()
    link_ids.reverse()
    return path, link_ids, best[goal][0]


def h_netinv_fiber_route(ctx, args):
    from_device_id = _req_id(args, "from_device_id")
    to_device_id = _req_id(args, "to_device_id")
    require(from_device_id != to_device_id, "invalid_argument")
    _get(ctx, "device", from_device_id)
    _get(ctx, "device", to_device_id)
    links = ctx.list("network_link", where={"status": "active"}, limit=LIST_CAP)
    adjacency = {}
    missing_length=0
    for link in links:
        a = link.get("a_device_id")
        b = link.get("b_device_id")
        length = link.get("length_m")
        if type(length) is not int or length < 0:
            missing_length+=1;continue
        weight=length
        adjacency.setdefault(a, []).append((b, link["id"], weight))
        adjacency.setdefault(b, []).append((a, link["id"], weight))
    result = _dijkstra(adjacency, from_device_id, to_device_id)
    if result is None:
        return {
            "reachable": False,
            "links_missing_length_ignored":missing_length,
            "inventory_complete":False,
            "path_device_ids": [],
            "link_ids": [],
            "total_length_m": None,
            "hops": 0,
            "coverage_promised": False,
            "analytics": _bounded(LIST_CAP),
            "note": "path search over EXISTING active link records only; unreachability is a statement about local inventory, never about real-world coverage",
        }
    path, link_ids, total = result
    return {
        "reachable": True,
        "links_missing_length_ignored":missing_length,
        "inventory_complete":False,
        "path_device_ids": path,
        "link_ids": link_ids,
        "total_length_m": total,
        "hops": len(link_ids),
        "coverage_promised": False,
        "analytics": _bounded(LIST_CAP),
        "note": "shortest path over existing recorded links by recorded length_m; inventory topology, not a service coverage guarantee",
    }


# ---------------------------------------------------------------------------
# netinv.splitter.management
# ---------------------------------------------------------------------------

_RATIOS = (4, 8, 16, 32, 64)


def h_netinv_splitter_management(ctx, args):
    action = _pick(args, "action", ("register", "allocate", "release", "inspect"))
    if action == "register":
        device_id = _req_id(args, "device_id")
        _get(ctx, "device", device_id)
        ratio = args.get("ratio")
        require(ratio in _RATIOS, "invalid_argument")
        name = args.get("name")
        if name is not None:
            name = _text(name, 100)
        existing = ctx.list("splitter", where={"device_id": device_id}, limit=LIST_CAP)
        require(not existing, "duplicate_splitter", 409)
        record = ctx.create(
            "splitter",
            {"device_id": device_id, "ratio": ratio, "total_ports": ratio, "name": name, "status": "active"},
        )
        ctx.event("netinv.splitter.registered", {"splitter_id": record["id"], "device_id": device_id})
        return {"splitter_id": record["id"], "device_id": device_id, "ratio": ratio, "total_ports": ratio, "status": "active"}

    splitter_id = _req_id(args, "splitter_id")
    splitter = _get(ctx, "splitter", splitter_id)
    total_ports = splitter["total_ports"]
    allocations = ctx.list("splitter_port", where={"splitter_id": splitter_id}, limit=LIST_CAP)
    active_alloc = {}
    for alloc in allocations:
        if alloc.get("status") == "allocated":
            active_alloc[alloc["port_no"]] = alloc

    if action == "inspect":
        allocated = [
            {"port_no": port, "subscriber_id": rec.get("subscriber_id")}
            for port, rec in sorted(active_alloc.items())
        ]
        return {
            "splitter_id": splitter_id,
            "device_id": splitter["device_id"],
            "ratio": splitter["ratio"],
            "total_ports": total_ports,
            "allocated_count": len(allocated),
            "free_count": total_ports - len(allocated),
            "utilization_bps": len(allocated) * 10000 // total_ports,
            "allocations": allocated,
            "analytics": _bounded(LIST_CAP),
        }

    if action == "allocate":
        subscriber_id = _opt_id(args, "subscriber_id")
        if subscriber_id is not None:
            _get(ctx, "subscriber", subscriber_id)
        port_no = args.get("port_no")
        if port_no is not None:
            port_no = _require_int(port_no, minimum=1, maximum=total_ports)
            require(port_no not in active_alloc, "port_unavailable", 409)
        else:
            port_no = next((p for p in range(1, total_ports + 1) if p not in active_alloc), None)
            require(port_no is not None, "splitter_capacity_exhausted", 409)
        record = ctx.create(
            "splitter_port",
            {
                "splitter_id": splitter_id,
                "port_no": port_no,
                "subscriber_id": subscriber_id,
                "status": "allocated",
                "allocated_at": ctx.now,
                "released_at": None,
            },
        )
        ctx.event("netinv.splitter.port.allocated", {"splitter_port_id": record["id"], "splitter_id": splitter_id})
        return {
            "splitter_port_id": record["id"],
            "splitter_id": splitter_id,
            "port_no": port_no,
            "subscriber_id": subscriber_id,
            "free_count": total_ports - len(active_alloc) - 1,
        }

    port_no = _require_int(args.get("port_no"), minimum=1, maximum=total_ports)
    alloc = active_alloc.get(port_no)
    require(alloc is not None, "not_allocated", 404)
    updated = ctx.update("splitter_port", alloc["id"], {"status": "released", "released_at": ctx.now}, alloc["version"])
    ctx.event("netinv.splitter.port.released", {"splitter_port_id": alloc["id"], "splitter_id": splitter_id})
    return {
        "splitter_port_id": alloc["id"],
        "splitter_id": splitter_id,
        "port_no": port_no,
        "status": updated["status"],
        "free_count": total_ports - len(active_alloc) + 1,
    }


# ---------------------------------------------------------------------------
# netinv.audit.physical
# ---------------------------------------------------------------------------


def h_netinv_audit_physical(ctx, args):
    items = args.get("items")
    require(isinstance(items, list) and 1 <= len(items) <= 500, "invalid_argument")
    matches = []
    discrepancies = []
    unknown = []
    duplicates = 0
    seen = set()
    for item in items:
        require(isinstance(item, dict), "invalid_argument")
        device_id = identifier(item.get("device_id"))
        found_status = _pick(item, "found_status", ("planned", "installed", "retired"))
        evidence_ref = item.get("evidence_ref")
        require(isinstance(evidence_ref, str) and 1 <= len(evidence_ref) <= 100, "invalid_argument")
        _iso_past(ctx, item, "observed_at", required=True)
        identifier(item.get("source"))
        key = (device_id, evidence_ref)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        try:
            local = ctx.get("device", device_id)
        except DomainError as exc:
            if exc.code == "not_found":
                unknown.append({"device_id": device_id, "evidence_ref": evidence_ref})
                continue
            raise
        if local["status"] == found_status:
            matches.append({"device_id": device_id, "status": found_status, "evidence_ref": evidence_ref})
        else:
            discrepancies.append(
                {
                    "device_id": device_id,
                    "local_status": local["status"],
                    "found_status": found_status,
                    "evidence_ref": evidence_ref,
                }
            )
    matches.sort(key=lambda r: r["device_id"])
    discrepancies.sort(key=lambda r: r["device_id"])
    unknown.sort(key=lambda r: r["device_id"])
    return {
        "items_total": len(items),
        "duplicates_ignored": duplicates,
        "match_count": len(matches),
        "matches": matches,
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
        "unknown_to_inventory": unknown,
        "correction_applied": False,
        "analytics": _bounded(500),
        "note": "compares submitted physical evidence against local records only; no local record is changed and no live device is queried",
    }


# ---------------------------------------------------------------------------
# noc.monitor.cpu
# ---------------------------------------------------------------------------


def h_noc_monitor_cpu(ctx, args):
    device_id = _req_id(args, "device_id")
    _get(ctx, "device", device_id)
    metric = _pick(args, "metric", ("cpu", "ram"), "invalid_argument") if args.get("metric") is not None else "cpu"
    warn_threshold = _require_int(args.get("warn_threshold", 80), minimum=1, maximum=100)
    max_age_s = _require_int(args.get("max_age_s", 3600), minimum=1, maximum=MAX_AGE_LIMIT_S)
    report = _evidence_samples(ctx, args.get("samples"), max_age_s, {"value_int": (0, 100)})
    fresh = report["fresh"]
    if fresh:
        latest = fresh[-1]
        breach = latest["value_int"] >= warn_threshold
        status = "degraded" if breach else "ok"
    else:
        latest = None
        breach = False
        status = "stale"
    return {
        "device_id": device_id,
        "metric": metric,
        "unit": "percent",
        "warn_threshold": warn_threshold,
        "samples_total": report["total"],
        "fresh_count": len(fresh),
        "stale_count": report["stale"],
        "duplicates_ignored": report["duplicates"],
        "future_ignored": report["future"],
        "latest_fresh": None if latest is None else {k: latest[k] for k in ("source", "observed_at", "age_s", "value_int")},
        "breach": breach,
        "status": status,
        "evidence_based": True,
        "live_poll_performed": False,
        "note": "operator-supplied samples with source+timestamp only; no SNMP/SSH poll, no live device state claim",
    }


# ---------------------------------------------------------------------------
# noc.map.network
# ---------------------------------------------------------------------------


def h_noc_map_network(ctx, args):
    root_device_id = _opt_id(args, "root_device_id")
    link_status = args.get("link_status")
    link_status = "all" if link_status is None else _pick({"link_status": link_status}, "link_status", ("active", "planned", "all"))
    devices = ctx.list("device", limit=LIST_CAP)
    links = ctx.list("network_link", limit=LIST_CAP)
    if link_status != "all":
        links = [l for l in links if l.get("status") == link_status]
    children = {d["id"]: [] for d in devices}
    by_id = {d["id"]: d for d in devices}
    for device in devices:
        parent = device.get("parent_id")
        if parent in children:
            children[parent].append(device["id"])
    if root_device_id is not None:
        _get(ctx, "device", root_device_id)
        keep = set()
        stack = [root_device_id]
        while stack:
            current = stack.pop()
            if current in keep:
                continue
            keep.add(current)
            stack.extend(children.get(current, ()))
        devices = [d for d in devices if d["id"] in keep]
        links = [
            l
            for l in links
            if l.get("a_device_id") in keep and l.get("b_device_id") in keep
        ]
    nodes = [
        {
            "device_id": d["id"],
            "name": d.get("name"),
            "device_type": d.get("device_type"),
            "status": d.get("status"),
            "parent_id": d.get("parent_id"),
        }
        for d in sorted(devices, key=lambda r: r["id"])
    ]
    edges = [
        {
            "link_id": l["id"],
            "a_device_id": l.get("a_device_id"),
            "b_device_id": l.get("b_device_id"),
            "link_type": l.get("link_type"),
            "length_m": l.get("length_m"),
            "status": l.get("status"),
        }
        for l in sorted(links, key=lambda r: r["id"])
    ]
    return {
        "root_device_id": root_device_id,
        "nodes": nodes,
        "edges": edges,
        "device_count": len(nodes),
        "link_count": len(edges),
        "live_state": False,
        "coverage_promised": False,
        "analytics": _bounded(LIST_CAP),
        "note": "static topology assembled from local inventory records; not live device state, not a coverage promise",
    }


# ---------------------------------------------------------------------------
# noc.shift.handover
# ---------------------------------------------------------------------------


def h_noc_shift_handover(ctx, args):
    shift_label = _text(args.get("shift_label"), 100, "invalid_argument", 1)
    operator_name = _text(args.get("operator_name"), 100, "invalid_argument", 1)
    require(not ctx.list("shift_handover", where={"shift_label": shift_label}, limit=LIST_CAP), "duplicate_shift_handover", 409)
    ticket_ids = _id_list(args, "ticket_ids", 100)
    outage_ids = _id_list(args, "outage_ids", 100)
    work_order_ids = _id_list(args, "work_order_ids", 100)
    tickets = []
    for tid in ticket_ids:
        rec = _get(ctx, "ticket", tid)
        tickets.append({"id": tid, "status": rec.get("status"), "severity": rec.get("severity")})
    outages = []
    for oid in outage_ids:
        rec = _get(ctx, "outage", oid)
        outages.append({"id": oid, "status": rec.get("status"), "severity": rec.get("severity")})
    work_orders = []
    for wid in work_order_ids:
        rec = _get(ctx, "work_order", wid)
        work_orders.append({"id": wid, "status": rec.get("status"), "priority": rec.get("priority")})
    notes = args.get("notes")
    if notes is not None:
        notes = _text(notes, 2000)
    record = ctx.create(
        "shift_handover",
        {
            "shift_label": shift_label,
            "operator_name": operator_name,
            "ticket_ids": ticket_ids,
            "outage_ids": outage_ids,
            "work_order_ids": work_order_ids,
            "notes": notes,
            "status": "recorded",
        },
    )
    ctx.event(
        "noc.shift.handover.recorded",
        {"handover_id": record["id"], "shift_label": shift_label, "open_items": len(tickets) + len(outages) + len(work_orders)},
    )
    return {
        "handover_id": record["id"],
        "shift_label": shift_label,
        "status": "recorded",
        "tickets": tickets,
        "outages": outages,
        "work_orders": work_orders,
        "note": "structured local handover record only; no notification is sent and no shift state is changed",
    }


# ---------------------------------------------------------------------------
# noc.incident.postmortem
# ---------------------------------------------------------------------------


def h_noc_incident_postmortem(ctx, args):
    ticket_id = _req_id(args, "ticket_id")
    ticket = _get(ctx, "ticket", ticket_id)
    require(ticket.get("status") == "resolved", "invalid_transition", 400)
    require(not ctx.list("incident_postmortem", where={"ticket_id": ticket_id}, limit=LIST_CAP), "duplicate_postmortem", 409)
    outage_id = _opt_id(args, "outage_id")
    if outage_id is not None:
        _get(ctx, "outage", outage_id)
    title = _text(args.get("title"), 200)
    summary = _text(args.get("summary"), 4000)
    category = _pick(
        args,
        "root_cause_category",
        ("fiber_cut", "power", "hardware", "configuration", "capacity", "external", "unknown"),
    )
    raw_actions = args.get("corrective_actions")
    require(isinstance(raw_actions, list) and 1 <= len(raw_actions) <= 20, "invalid_argument")
    corrective_actions = [_text(a, 500) for a in raw_actions]
    raw_timeline = args.get("timeline")
    timeline = []
    if raw_timeline is not None:
        require(isinstance(raw_timeline, list) and len(raw_timeline) <= 50, "invalid_argument")
        for entry in raw_timeline:
            require(isinstance(entry, dict), "invalid_argument")
            at = _iso_past(ctx, entry, "at", required=True)
            timeline.append({"at": at, "description": _text(entry.get("description"), 500)})
    timeline.sort(key=lambda e: e["at"])
    affected = args.get("affected_subscribers")
    if affected is not None:
        affected = _require_int(affected, minimum=0, maximum=10 ** 9)
    record = ctx.create(
        "incident_postmortem",
        {
            "ticket_id": ticket_id,
            "outage_id": outage_id,
            "title": title,
            "summary": summary,
            "root_cause_category": category,
            "corrective_actions": corrective_actions,
            "timeline": timeline,
            "affected_subscribers": affected,
            "status": "recorded",
            "cause_verified": False,
        },
    )
    ctx.event("noc.incident.postmortem.recorded", {"postmortem_id": record["id"], "ticket_id": ticket_id})
    return {
        "postmortem_id": record["id"],
        "ticket_id": ticket_id,
        "status": "recorded",
        "cause_verified": False,
        "human_review_required": True,
        "note": "operator-supplied analysis recorded against a locally resolved ticket; root cause is not independently verified",
    }


# ---------------------------------------------------------------------------
# field.quality.score
# ---------------------------------------------------------------------------


def h_field_quality_score(ctx, args):
    technician_id = _req_id(args, "technician_id")
    work_order_id = _opt_id(args, "work_order_id")
    if work_order_id is not None:
        _get(ctx, "work_order", work_order_id)
    checklist = args.get("checklist")
    require(isinstance(checklist, list) and 1 <= len(checklist) <= 100, "invalid_argument")
    total_weight = 0
    passed_weight = 0
    failed = []
    seen_items = set()
    for item in checklist:
        require(isinstance(item, dict), "invalid_argument")
        item_id = identifier(item.get("item_id"))
        require(item_id not in seen_items, "duplicate_checklist_item")
        seen_items.add(item_id)
        passed = item.get("passed")
        require(type(passed) is bool, "invalid_argument")
        weight = _require_int(item.get("weight"), minimum=1, maximum=100)
        total_weight += weight
        if passed:
            passed_weight += weight
        else:
            failed.append(item_id)
    threshold = _require_int(args.get("pass_threshold_bps", 7000), minimum=1, maximum=10000)
    score_bps = passed_weight * 10000 // total_weight
    source = identifier(args.get("source"))
    recorded_at = _iso_past(ctx, args, "recorded_at") or ctx.now
    quality_score_id = None
    if args.get("record") is True:
        record = ctx.create(
            "quality_score",
            {
                "technician_id": technician_id,
                "work_order_id": work_order_id,
                "score_bps": score_bps,
                "pass_threshold_bps": threshold,
                "passed": score_bps >= threshold,
                "items_total": len(checklist),
                "items_passed": len(checklist) - len(failed),
                "failed_item_ids": sorted(failed),
                "source": source,
                "recorded_at": recorded_at,
            },
        )
        quality_score_id = record["id"]
        ctx.event("field.quality.score.recorded", {"quality_score_id": quality_score_id, "technician_id": technician_id})
    return {
        "technician_id": technician_id,
        "work_order_id": work_order_id,
        "score_bps": score_bps,
        "score_percent_text": "%d.%02d" % (score_bps // 100, score_bps % 100),
        "pass_threshold_bps": threshold,
        "passed": score_bps >= threshold,
        "items_total": len(checklist),
        "items_passed": len(checklist) - len(failed),
        "failed_item_ids": sorted(failed),
        "source": source,
        "recorded": quality_score_id is not None,
        "quality_score_id": quality_score_id,
        "note": "weighted scoring of SUPPLIED checklist evidence only; no field observation is performed",
    }


# ---------------------------------------------------------------------------
# field.schedule.optimize
# ---------------------------------------------------------------------------


def h_field_schedule_optimize(ctx, args):
    raw_orders = args.get("work_orders")
    require(isinstance(raw_orders, list) and 1 <= len(raw_orders) <= 100, "invalid_argument")
    orders = []
    order_ids = set()
    for item in raw_orders:
        require(isinstance(item, dict), "invalid_argument")
        work_order_id = identifier(item.get("work_order_id"))
        require(work_order_id not in order_ids, "duplicate_work_order")
        order_ids.add(work_order_id)
        _get(ctx, "work_order", work_order_id)
        duration = _require_int(item.get("duration_minutes"), minimum=15, maximum=1440)
        priority = item.get("priority")
        priority = 50 if priority is None else _require_int(priority, minimum=0, maximum=100)
        orders.append({"work_order_id": work_order_id, "duration_minutes": duration, "priority": priority})
    raw_techs = args.get("technicians")
    require(isinstance(raw_techs, list) and 1 <= len(raw_techs) <= 50, "invalid_argument")
    technicians = {}
    for item in raw_techs:
        require(isinstance(item, dict), "invalid_argument")
        technician_id = identifier(item.get("technician_id"))
        require(technician_id not in technicians, "duplicate_technician")
        from_raw = item.get("available_from")
        to_raw = item.get("available_to")
        require(isinstance(from_raw, str) and isinstance(to_raw, str), "invalid_argument")
        available_from = parse_time(from_raw)
        available_to = parse_time(to_raw)
        require(available_from < available_to, "invalid_window")
        technicians[technician_id] = {"available_from": available_from, "available_to": available_to, "cursor": available_from}
    orders.sort(key=lambda o: (o["priority"], -o["duration_minutes"], o["work_order_id"]))
    assignments = []
    unassigned = []
    for order in orders:
        duration = _dt.timedelta(minutes=order["duration_minutes"])
        best = None
        for technician_id in sorted(technicians):
            tech = technicians[technician_id]
            start = max(tech["cursor"], tech["available_from"])
            if start + duration <= tech["available_to"]:
                candidate = (start, technician_id)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            unassigned.append(order["work_order_id"])
            continue
        start, technician_id = best
        end = start + duration
        technicians[technician_id]["cursor"] = end
        assignments.append(
            {
                "work_order_id": order["work_order_id"],
                "technician_id": technician_id,
                "priority": order["priority"],
                "duration_minutes": order["duration_minutes"],
                "proposed_start": _iso(start),
                "proposed_end": _iso(end),
            }
        )
    assignments.sort(key=lambda a: (a["proposed_start"], a["technician_id"], a["work_order_id"]))
    return {
        "assignments": assignments,
        "unassigned_work_order_ids": sorted(unassigned),
        "method": "greedy_earliest_fit_deterministic",
        "booked": False,
        "note": "non-overlap proposal per technician from supplied availability windows only; no booking, dispatch or calendar write is performed",
    }


# ---------------------------------------------------------------------------
# qos.threshold
# ---------------------------------------------------------------------------


def h_qos_threshold(ctx, args):
    product_id = _opt_id(args, "product_id")
    if product_id is None:
        profiles = sorted(ctx.list("qos_threshold_profile", limit=LIST_CAP), key=lambda r: r["id"])
        return {
            "profiles": profiles,
            "count": len(profiles),
            "analytics": _bounded(LIST_CAP),
            "note": "list of local threshold policy profiles only",
        }
    product = _get(ctx, "product", product_id)
    require(product.get("active") is not False, "product_inactive", 409)
    thresholds_raw = args.get("thresholds")
    require(isinstance(thresholds_raw, dict), "invalid_argument")
    thresholds = {
        "latency_ms": _require_int(thresholds_raw.get("latency_ms"), minimum=1, maximum=60000),
        "jitter_ms": _require_int(thresholds_raw.get("jitter_ms"), minimum=1, maximum=5000),
        "loss_bps": _require_int(thresholds_raw.get("loss_bps"), minimum=1, maximum=10000),
    }
    name = args.get("name")
    if name is not None:
        name = _text(name, 100)
    superseded = []
    for existing in ctx.list("qos_threshold_profile", where={"product_id": product_id, "status": "active"}, limit=LIST_CAP):
        require(args.get("replace") is True, "duplicate_threshold_profile", 409)
        ctx.update("qos_threshold_profile", existing["id"], {"status": "superseded"}, existing["version"])
        superseded.append(existing["id"])
    record = ctx.create(
        "qos_threshold_profile",
        {
            "product_id": product_id,
            "name": name,
            "thresholds": thresholds,
            "status": "active",
            "enforcement": "local_flag_only",
        },
    )
    ctx.event("qos.threshold.profile.set", {"profile_id": record["id"], "product_id": product_id})
    return {
        "profile_id": record["id"],
        "product_id": product_id,
        "thresholds": thresholds,
        "status": "active",
        "superseded_profile_ids": superseded,
        "enforcement": "local_flag_only",
        "note": "local policy profile only; no device/network configuration is pushed",
    }


# ---------------------------------------------------------------------------
# qos.benchmark
# ---------------------------------------------------------------------------


def h_qos_benchmark(ctx, args):
    benchmark_raw = args.get("benchmark")
    require(isinstance(benchmark_raw, dict), "invalid_argument")
    benchmark = {
        "latency_ms": _require_int(benchmark_raw.get("latency_ms"), minimum=1, maximum=60000),
        "jitter_ms": _require_int(benchmark_raw.get("jitter_ms"), minimum=1, maximum=5000),
        "loss_bps": _require_int(benchmark_raw.get("loss_bps"), minimum=1, maximum=10000),
    }
    max_age_s = _require_int(args.get("max_age_s", 3600), minimum=1, maximum=MAX_AGE_LIMIT_S)
    report = _evidence_samples(
        ctx,
        args.get("samples"),
        max_age_s,
        {"latency_ms": (0, 60000), "jitter_ms": (0, 5000), "loss_bps": (0, 10000)},
    )
    fresh = report["fresh"]
    label = args.get("label")
    if label is not None:
        label = _text(label, 100)
    if fresh:
        count = len(fresh)
        averages = {
            "latency_ms": sum(s["latency_ms"] for s in fresh) // count,
            "jitter_ms": sum(s["jitter_ms"] for s in fresh) // count,
            "loss_bps": sum(s["loss_bps"] for s in fresh) // count,
        }
        breaches = sorted(m for m in ("latency_ms", "jitter_ms", "loss_bps") if averages[m] > benchmark[m])
        meets = not breaches
        status = "degraded" if breaches else "ok"
    else:
        averages = {"latency_ms": None, "jitter_ms": None, "loss_bps": None}
        breaches = []
        meets = None
        status = "stale"
    return {
        "label": label,
        "samples_total": report["total"],
        "fresh_count": len(fresh),
        "stale_count": report["stale"],
        "duplicates_ignored": report["duplicates"],
        "future_ignored": report["future"],
        "measured_averages": averages,
        "benchmark": benchmark,
        "breached_metrics": breaches,
        "meets_benchmark": meets,
        "status": status,
        "external_data_used": False,
        "note": "compares operator-supplied measurements against operator-supplied benchmark targets; no competitor or external data is fetched",
    }


# ---------------------------------------------------------------------------
# fraud.case.investigate
# ---------------------------------------------------------------------------


def h_fraud_case_investigate(ctx, args):
    case_ref = args.get("case_ref")
    require(isinstance(case_ref, str) and 1 <= len(case_ref) <= 100, "invalid_argument")
    require(not ctx.list("fraud_investigation", where={"case_ref": case_ref}, limit=LIST_CAP), "duplicate_case", 409)
    subscriber_id = _opt_id(args, "subscriber_id")
    if subscriber_id is not None:
        _get(ctx, "subscriber", subscriber_id)
    allegation = _pick(args, "allegation", ("usage_anomaly", "payment_fraud", "identity_fraud", "theft_of_service", "other"))
    raw_evidence = args.get("evidence")
    require(isinstance(raw_evidence, list) and 1 <= len(raw_evidence) <= 50, "invalid_argument")
    evidence = []
    seen_refs = set()
    for item in raw_evidence:
        require(isinstance(item, dict), "invalid_argument")
        evidence_ref = item.get("evidence_ref")
        require(isinstance(evidence_ref, str) and 1 <= len(evidence_ref) <= 100, "invalid_argument")
        require(evidence_ref not in seen_refs, "duplicate_evidence")
        seen_refs.add(evidence_ref)
        observed_at = _iso_past(ctx, item, "observed_at")
        source = _opt_id(item, "source")
        evidence.append(
            {
                "evidence_ref": evidence_ref,
                "description": _text(item.get("description"), 500),
                "observed_at": observed_at,
                "source": source,
            }
        )
    notes = args.get("notes")
    if notes is not None:
        notes = _text(notes, 2000)
    record = ctx.create(
        "fraud_investigation",
        {
            "case_ref": case_ref,
            "subscriber_id": subscriber_id,
            "allegation": allegation,
            "evidence": evidence,
            "notes": notes,
            "status": "under_review",
            "outcome": "pending_human_decision",
            "guilty_determination": None,
            "verdict_recorded": False,
        },
    )
    ctx.event("fraud.case.opened", {"investigation_id": record["id"], "case_ref": case_ref})
    return {
        "investigation_id": record["id"],
        "case_ref": case_ref,
        "status": "under_review",
        "outcome": "pending_human_decision",
        "guilty_determination": None,
        "verdict_recorded": False,
        "note": "review case record only; no guilt verdict, no subscriber status change, no blacklist",
    }


# ---------------------------------------------------------------------------
# fraud.blacklist.subscriber
# ---------------------------------------------------------------------------


def h_fraud_blacklist_subscriber(ctx, args):
    subscriber_id = _req_id(args, "subscriber_id")
    _get(ctx, "subscriber", subscriber_id)
    reason = _pick(args, "reason", ("chargeback_fraud", "payment_fraud", "identity_fraud", "device_theft", "other"))
    evidence_refs = _id_list(args, "evidence_refs", 20)
    require(evidence_refs, "invalid_argument")
    justification = _text(args.get("justification"), 1000)
    for existing in ctx.list("blacklist_review_request", where={"subscriber_id": subscriber_id}, limit=LIST_CAP):
        require(existing.get("status") != "requested", "open_request_exists", 409)
    record = ctx.create(
        "blacklist_review_request",
        {
            "subscriber_id": subscriber_id,
            "reason": reason,
            "evidence_refs": evidence_refs,
            "justification": justification,
            "requested_by": ctx.actor,
            "requested_at": ctx.now,
            "status": "requested",
            "decision": "pending_human_approval",
            "blacklisted": False,
            "effective": False,
        },
    )
    ctx.event("fraud.blacklist.review.requested", {"request_id": record["id"], "subscriber_id": subscriber_id})
    return {
        "request_id": record["id"],
        "subscriber_id": subscriber_id,
        "status": "requested",
        "decision": "pending_human_approval",
        "blacklisted": False,
        "effective": False,
        "review_only": True,
        "note": "LOCAL REVIEW REQUEST only; the subscriber is NOT blacklisted and no service is changed by this handler",
    }


# ---------------------------------------------------------------------------
# revass.discount.abuse
# ---------------------------------------------------------------------------


def h_revass_discount_abuse(ctx, args):
    policy_raw = args.get("policy")
    require(isinstance(policy_raw, dict), "invalid_argument")
    max_bps = _require_int(policy_raw.get("max_discount_bps"), minimum=1, maximum=10000)
    discounts = args.get("discounts")
    require(isinstance(discounts, list) and 1 <= len(discounts) <= 500, "invalid_argument")
    findings = []
    duplicates = []
    seen_refs = set()
    for item in discounts:
        require(isinstance(item, dict), "invalid_argument")
        discount_ref = item.get("discount_ref")
        require(isinstance(discount_ref, str) and 1 <= len(discount_ref) <= 100, "invalid_argument")
        subscriber_id = _opt_id(item, "subscriber_id")
        if subscriber_id is not None:
            _get(ctx, "subscriber", subscriber_id)
        discount_bps = _require_int(item.get("discount_bps"), minimum=0, maximum=10000)
        approved_raw = item.get("approved_bps")
        approved_bps = None if approved_raw is None else _require_int(approved_raw, minimum=0, maximum=10000)
        period = item.get("period")
        require(isinstance(period, str) and _PERIOD_RE.fullmatch(period) is not None, "invalid_period")
        if discount_ref in seen_refs:
            duplicates.append(discount_ref)
            continue
        seen_refs.add(discount_ref)
        reasons = []
        if discount_bps > max_bps:
            reasons.append("exceeds_policy_max")
        if approved_bps is None:
            reasons.append("approval_not_evidenced")
        elif discount_bps > approved_bps:
            reasons.append("exceeds_approved")
        if reasons:
            findings.append({"discount_ref": discount_ref, "subscriber_id": subscriber_id, "discount_bps": discount_bps, "reasons": sorted(reasons)})
    findings.sort(key=lambda f: f["discount_ref"])
    return {
        "discounts_total": len(discounts),
        "unique_discounts": len(seen_refs),
        "duplicate_refs": sorted(duplicates),
        "policy": {"max_discount_bps": max_bps},
        "finding_count": len(findings),
        "findings": findings,
        "abuse_indicated": bool(findings) or bool(duplicates),
        "revocation_executed": False,
        "analytics": _bounded(500),
        "note": "flags supplied discount records against supplied policy; no discount is changed and no money is moved",
    }


# ---------------------------------------------------------------------------
# revass.report.monthly
# ---------------------------------------------------------------------------

_CATEGORIES = ("revenue", "discount", "tax", "other")


def h_revass_report_monthly(ctx, args):
    period = args.get("period")
    require(isinstance(period, str) and _PERIOD_RE.fullmatch(period) is not None, "invalid_period")
    entries = args.get("entries")
    require(isinstance(entries, list) and 1 <= len(entries) <= 1000, "invalid_argument")
    totals = {c: {"expected_idr": 0, "actual_idr": 0, "variance_idr": 0, "entry_count": 0} for c in _CATEGORIES}
    discrepancies = []
    duplicates = []
    seen_refs = set()
    for item in entries:
        require(isinstance(item, dict), "invalid_argument")
        entry_ref = item.get("entry_ref")
        require(isinstance(entry_ref, str) and 1 <= len(entry_ref) <= 100, "invalid_argument")
        category = _pick(item, "category", _CATEGORIES)
        expected = money(item.get("expected_idr"))
        actual = money(item.get("actual_idr"))
        if entry_ref in seen_refs:
            duplicates.append(entry_ref)
        seen_refs.add(entry_ref)
        variance = actual - expected
        bucket = totals[category]
        bucket["expected_idr"] += expected
        bucket["actual_idr"] += actual
        bucket["variance_idr"] += variance
        bucket["entry_count"] += 1
        if variance != 0:
            discrepancies.append({"entry_ref": entry_ref, "category": category, "expected_idr": expected, "actual_idr": actual, "variance_idr": variance})
    discrepancies.sort(key=lambda d: d["entry_ref"])
    total_expected = sum(t["expected_idr"] for t in totals.values())
    total_actual = sum(t["actual_idr"] for t in totals.values())
    return {
        "period": period,
        "totals_by_category": totals,
        "total_expected_idr": total_expected,
        "total_actual_idr": total_actual,
        "total_variance_idr": total_actual - total_expected,
        "entry_count": len(entries),
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
        "duplicate_refs": sorted(set(duplicates)),
        "status": "draft",
        "submitted": False,
        "source": "supplied_reconciliation_evidence",
        "analytics": _bounded(1000),
        "note": "integer-exact summary of SUPPLIED reconciliation evidence; draft only, no filing or submission of any kind",
    }


# ---------------------------------------------------------------------------
# revass.recovery.action
# ---------------------------------------------------------------------------


def h_revass_recovery_action(ctx, args):
    case_ref = args.get("case_ref")
    require(isinstance(case_ref, str) and 1 <= len(case_ref) <= 100, "invalid_argument")
    action_type = _pick(
        args,
        "action_type",
        ("offset_next_invoice", "credit_note_request", "writeoff_request", "external_collection_referral"),
    )
    amount_idr = money(args.get("amount_idr"))
    subscriber_id = _opt_id(args, "subscriber_id")
    if subscriber_id is not None:
        _get(ctx, "subscriber", subscriber_id)
    invoice_id = _opt_id(args, "invoice_id")
    if invoice_id is not None:
        invoice = _get(ctx, "invoice", invoice_id)
        if subscriber_id is not None:
            require(invoice.get("subscriber_id") == subscriber_id, "subscriber_invoice_mismatch")
    justification = _text(args.get("justification"), 1000)
    for existing in ctx.list("recovery_request", where={"case_ref": case_ref}, limit=LIST_CAP):
        require(existing.get("status") != "pending_approval", "open_request_exists", 409)
    record = ctx.create(
        "recovery_request",
        {
            "case_ref": case_ref,
            "action_type": action_type,
            "amount_idr": amount_idr,
            "subscriber_id": subscriber_id,
            "invoice_id": invoice_id,
            "justification": justification,
            "requested_by": ctx.actor,
            "requested_at": ctx.now,
            "status": "pending_approval",
            "executed": False,
            "amount_recovered_idr": 0,
            "money_moved": False,
        },
    )
    ctx.event("revass.recovery.requested", {"request_id": record["id"], "case_ref": case_ref})
    return {
        "request_id": record["id"],
        "case_ref": case_ref,
        "status": "pending_approval",
        "amount_idr": amount_idr,
        "approval_required": True,
        "executed": False,
        "money_movement_performed": False,
        "note": "LOCAL APPROVAL REQUEST only; no invoice is changed, no refund/offset/write-off executes and no money moves",
    }


# ---------------------------------------------------------------------------
# workflow.sla.attach
# ---------------------------------------------------------------------------


def h_workflow_sla_attach(ctx, args):
    sla_id = _req_id(args, "sla_id")
    _get(ctx, "sla", sla_id)
    template_id = _opt_id(args, "template_id")
    instance_id = _opt_id(args, "instance_id")
    require((template_id is None) != (instance_id is None), "invalid_argument")
    if template_id is not None:
        _get(ctx, "workflow_template", template_id)
        target_kind = "workflow_template_id"
        target_id = template_id
    else:
        _get(ctx, "workflow_instance", instance_id)
        target_kind = "workflow_instance_id"
        target_id = instance_id
    for existing in ctx.list("workflow_sla_binding", where={target_kind: target_id}, limit=LIST_CAP):
        require(False, "already_attached", 409)
    record = ctx.create(
        "workflow_sla_binding",
        {
            "sla_id": sla_id,
            "workflow_template_id": template_id,
            "workflow_instance_id": instance_id,
            "status": "attached",
            "enforcement": "local_evaluation_only",
        },
    )
    ctx.event("workflow.sla.attached", {"binding_id": record["id"], "sla_id": sla_id, target_kind: target_id})
    return {
        "binding_id": record["id"],
        "sla_id": sla_id,
        target_kind: target_id,
        "status": "attached",
        "enforcement": "local_evaluation_only",
        "note": "FK-checked binding between existing local SLA and existing local workflow record; no workflow behavior changes automatically",
    }


# ---------------------------------------------------------------------------
# Module contract: HANDLERS / SCHEMAS / MUTATIONS / LIMITATIONS
# ---------------------------------------------------------------------------

_CPU_SAMPLE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "observed_at", "value_int"],
    "properties": {"source": _ID, "observed_at": _ISO, "value_int": {"type": "integer", "minimum": 0, "maximum": 100}},
}

_QOS_SAMPLE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "observed_at", "latency_ms", "jitter_ms", "loss_bps"],
    "properties": {
        "source": _ID,
        "observed_at": _ISO,
        "latency_ms": {"type": "integer", "minimum": 0, "maximum": 60000},
        "jitter_ms": {"type": "integer", "minimum": 0, "maximum": 5000},
        "loss_bps": {"type": "integer", "minimum": 0, "maximum": 10000},
    },
}

_EXTENSIONS = (
    (
        "netinv.fiber.route",
        h_netinv_fiber_route,
        _sch({"from_device_id": _ID, "to_device_id": _ID}, ["from_device_id", "to_device_id"]),
        False,
        "Shortest path over EXISTING local network_link records by recorded length; unreachability reflects local inventory only and coverage is NEVER promised. Read-only.",
    ),
    (
        "netinv.splitter.management",
        h_netinv_splitter_management,
        _sch(
            {
                "action": {"type": "string", "enum": ["register", "allocate", "release", "inspect"]},
                "device_id": _ID,
                "ratio": {"type": "integer", "enum": [4, 8, 16, 32, 64]},
                "name": {"type": "string", "minLength": 1, "maxLength": 100},
                "splitter_id": _ID,
                "port_no": {"type": "integer", "minimum": 1, "maximum": 64},
                "subscriber_id": _ID,
            },
            ["action"],
        ),
        True,
        "Local bounded splitter inventory: fixed ratios 1:4..1:64, port accounting within tenant records (max 1000 listed); no physical splitter is configured or touched.",
    ),
    (
        "netinv.audit.physical",
        h_netinv_audit_physical,
        _sch(
            {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 500,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["device_id", "found_status", "evidence_ref", "observed_at", "source"],
                        "properties": {
                            "device_id": _ID,
                            "found_status": {"type": "string", "enum": ["planned", "installed", "retired"]},
                            "evidence_ref": _REF,
                            "observed_at": _ISO,
                            "source": _ID,
                        },
                    },
                }
            },
            ["items"],
        ),
        False,
        "Compares SUBMITTED physical audit evidence vs local device records; unknown devices are reported as findings, local records are never auto-corrected and no live device is queried.",
    ),
    (
        "noc.monitor.cpu",
        h_noc_monitor_cpu,
        _sch(
            {
                "device_id": _ID,
                "metric": {"type": "string", "enum": ["cpu", "ram"]},
                "samples": {"type": "array", "minItems": 1, "maxItems": 1000, "items": _CPU_SAMPLE},
                "warn_threshold": {"type": "integer", "minimum": 1, "maximum": 100},
                "max_age_s": {"type": "integer", "minimum": 1, "maximum": MAX_AGE_LIMIT_S},
            },
            ["device_id", "samples"],
        ),
        False,
        "Evaluates operator-supplied CPU/RAM percent samples (source+timestamp, freshness window, duplicate/future/stale rejection); NO SNMP/SSH poll is performed and status is evidence-based only. Default warn 80% is heuristic.",
    ),
    (
        "noc.map.network",
        h_noc_map_network,
        _sch(
            {
                "root_device_id": _ID,
                "link_status": {"type": "string", "enum": ["active", "planned", "all"]},
            },
            [],
        ),
        False,
        "Static topology assembled from local device + network_link records (bounded 1000 per kind); not live device state and not a coverage promise.",
    ),
    (
        "noc.shift.handover",
        h_noc_shift_handover,
        _sch(
            {
                "shift_label": {"type": "string", "minLength": 1, "maxLength": 100},
                "operator_name": {"type": "string", "minLength": 1, "maxLength": 100},
                "ticket_ids": {"type": "array", "maxItems": 100, "items": _ID},
                "outage_ids": {"type": "array", "maxItems": 100, "items": _ID},
                "work_order_ids": {"type": "array", "maxItems": 100, "items": _ID},
                "notes": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            ["shift_label", "operator_name"],
        ),
        True,
        "Records one structured handover per unique shift_label with FK-checked local ticket/outage/work_order references; no notification is sent and no shift system is changed.",
    ),
    (
        "noc.incident.postmortem",
        h_noc_incident_postmortem,
        _sch(
            {
                "ticket_id": _ID,
                "outage_id": _ID,
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
                "root_cause_category": {
                    "type": "string",
                    "enum": ["fiber_cut", "power", "hardware", "configuration", "capacity", "external", "unknown"],
                },
                "corrective_actions": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 500}},
                "timeline": {
                    "type": "array",
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["at", "description"],
                        "properties": {"at": _ISO, "description": {"type": "string", "minLength": 1, "maxLength": 500}},
                    },
                },
                "affected_subscribers": {"type": "integer", "minimum": 0, "maximum": 10 ** 9},
            },
            ["ticket_id", "title", "summary", "root_cause_category", "corrective_actions"],
        ),
        True,
        "Records operator-supplied post-mortem analysis against a locally RESOLVED ticket (one per ticket); root cause is not independently verified and no external report is filed.",
    ),
    (
        "field.quality.score",
        h_field_quality_score,
        _sch(
            {
                "technician_id": _ID,
                "work_order_id": _ID,
                "checklist": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["item_id", "passed", "weight"],
                        "properties": {
                            "item_id": _ID,
                            "passed": {"type": "boolean"},
                            "weight": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                    },
                },
                "source": _ID,
                "recorded_at": _ISO,
                "pass_threshold_bps": {"type": "integer", "minimum": 1, "maximum": 10000},
                "record": {"type": "boolean"},
            },
            ["technician_id", "checklist", "source"],
        ),
        True,
        "Scores SUPPLIED checklist evidence in integer basis points (weights 1..100); writes a local quality_score record only when record=true. No field observation is performed.",
    ),
    (
        "field.schedule.optimize",
        h_field_schedule_optimize,
        _sch(
            {
                "work_orders": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["work_order_id", "duration_minutes"],
                        "properties": {
                            "work_order_id": _ID,
                            "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 1440},
                            "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                        },
                    },
                },
                "technicians": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["technician_id", "available_from", "available_to"],
                        "properties": {
                            "technician_id": _ID,
                            "available_from": _ISO,
                            "available_to": _ISO,
                        },
                    },
                },
            },
            ["work_orders", "technicians"],
        ),
        False,
        "Deterministic greedy earliest-fit proposal from supplied availability windows; guarantees non-overlap per technician, is not an optimal scheduler, and performs NO booking or dispatch write.",
    ),
    (
        "qos.threshold",
        h_qos_threshold,
        _sch(
            {
                "product_id": _ID,
                "name": {"type": "string", "minLength": 1, "maxLength": 100},
                "thresholds": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["latency_ms", "jitter_ms", "loss_bps"],
                    "properties": {
                        "latency_ms": {"type": "integer", "minimum": 1, "maximum": 60000},
                        "jitter_ms": {"type": "integer", "minimum": 1, "maximum": 5000},
                        "loss_bps": {"type": "integer", "minimum": 1, "maximum": 10000},
                    },
                },
                "replace": {"type": "boolean"},
            },
            [],
        ),
        True,
        "Local threshold policy profile per existing product (one active per product unless replace=true supersedes); flags only, no device/network configuration is pushed. Omit product_id to list existing profiles.",
    ),
    (
        "qos.benchmark",
        h_qos_benchmark,
        _sch(
            {
                "samples": {"type": "array", "minItems": 1, "maxItems": 1000, "items": _QOS_SAMPLE},
                "benchmark": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["latency_ms", "jitter_ms", "loss_bps"],
                    "properties": {
                        "latency_ms": {"type": "integer", "minimum": 1, "maximum": 60000},
                        "jitter_ms": {"type": "integer", "minimum": 1, "maximum": 5000},
                        "loss_bps": {"type": "integer", "minimum": 1, "maximum": 10000},
                    },
                },
                "label": {"type": "string", "minLength": 1, "maxLength": 100},
                "max_age_s": {"type": "integer", "minimum": 1, "maximum": MAX_AGE_LIMIT_S},
            },
            ["samples", "benchmark"],
        ),
        False,
        "Compares operator-supplied measurements against operator-supplied benchmark targets with freshness/duplicate handling; NO competitor, external or live data is fetched.",
    ),
    (
        "fraud.case.investigate",
        h_fraud_case_investigate,
        _sch(
            {
                "case_ref": _REF,
                "subscriber_id": _ID,
                "allegation": {
                    "type": "string",
                    "enum": ["usage_anomaly", "payment_fraud", "identity_fraud", "theft_of_service", "other"],
                },
                "evidence": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["evidence_ref", "description"],
                        "properties": {
                            "evidence_ref": _REF,
                            "description": {"type": "string", "minLength": 1, "maxLength": 500},
                            "observed_at": _ISO,
                            "source": _ID,
                        },
                    },
                },
                "notes": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            ["case_ref", "allegation", "evidence"],
        ),
        True,
        "Records a fraud REVIEW case with supplied evidence references; outcome stays pending_human_decision, NEVER a guilty verdict, no subscriber status change and no blacklist.",
    ),
    (
        "fraud.blacklist.subscriber",
        h_fraud_blacklist_subscriber,
        _sch(
            {
                "subscriber_id": _ID,
                "reason": {"type": "string", "enum": ["chargeback_fraud", "payment_fraud", "identity_fraud", "device_theft", "other"]},
                "evidence_refs": {"type": "array", "minItems": 1, "maxItems": 20, "items": _REF},
                "justification": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
            ["subscriber_id", "reason", "evidence_refs", "justification"],
        ),
        True,
        "LOCAL REVIEW REQUEST only: creates a pending_human_approval request record. The subscriber is NEVER blacklisted, no service is suspended and no external system is touched.",
    ),
    (
        "revass.discount.abuse",
        h_revass_discount_abuse,
        _sch(
            {
                "policy": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["max_discount_bps"],
                    "properties": {"max_discount_bps": {"type": "integer", "minimum": 1, "maximum": 10000}},
                },
                "discounts": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 500,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["discount_ref", "discount_bps", "period"],
                        "properties": {
                            "discount_ref": _REF,
                            "subscriber_id": _ID,
                            "discount_bps": {"type": "integer", "minimum": 0, "maximum": 10000},
                            "approved_bps": {"type": "integer", "minimum": 0, "maximum": 10000},
                            "period": _PERIOD,
                        },
                    },
                },
            },
            ["policy", "discounts"],
        ),
        False,
        "Flags supplied discount records against the supplied policy (policy max, evidenced approval); no discount is revoked and no money is moved.",
    ),
    (
        "revass.report.monthly",
        h_revass_report_monthly,
        _sch(
            {
                "period": _PERIOD,
                "entries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1000,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["entry_ref", "category", "expected_idr", "actual_idr"],
                        "properties": {
                            "entry_ref": _REF,
                            "category": {"type": "string", "enum": ["revenue", "discount", "tax", "other"]},
                            "expected_idr": _MONEY,
                            "actual_idr": _MONEY,
                        },
                    },
                },
            },
            ["period", "entries"],
        ),
        False,
        "Integer-exact reconciliation summary of SUPPLIED evidence for one period; draft only, never filed or submitted anywhere. No tax rate is assumed.",
    ),
    (
        "revass.recovery.action",
        h_revass_recovery_action,
        _sch(
            {
                "case_ref": _REF,
                "action_type": {
                    "type": "string",
                    "enum": ["offset_next_invoice", "credit_note_request", "writeoff_request", "external_collection_referral"],
                },
                "amount_idr": _MONEY,
                "subscriber_id": _ID,
                "invoice_id": _ID,
                "justification": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
            ["case_ref", "action_type", "amount_idr", "justification"],
        ),
        True,
        "LOCAL approval request record only: no invoice change, no refund/offset/write-off execution, no money movement; requires human approval by design.",
    ),
    (
        "workflow.sla.attach",
        h_workflow_sla_attach,
        _sch(
            {
                "sla_id": _ID,
                "template_id": _ID,
                "instance_id": _ID,
            },
            ["sla_id"],
        ),
        True,
        "FK-checked binding of an EXISTING local SLA record to an EXISTING local workflow template or instance (exactly one target); local evaluation marker only, nothing executes automatically.",
    ),
)

HANDLERS = {}
SCHEMAS = {}
LIMITATIONS = {}
_mutations = set()
for _handler_id, _handler, _schema, _is_mutation, _limitation in _EXTENSIONS:
    assert _handler_id not in HANDLERS, "duplicate extension handler"
    HANDLERS[_handler_id] = _handler
    SCHEMAS[_handler_id] = _schema
    LIMITATIONS[_handler_id] = _limitation
    if _is_mutation:
        _mutations.add(_handler_id)
assert set(HANDLERS) == set(SCHEMAS), "schema coverage mismatch"
MUTATIONS = frozenset(_mutations)

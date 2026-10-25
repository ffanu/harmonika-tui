"""Harmonika telco OPERATIONS domain engine (worker-owned module).

Scope: engines 1, 2, 3, 11, 12 from the catalog in catalog_source.json
(network inventory, local provisioning planning, NOC evidence handling,
field operations, service assurance/SLA).

Hard boundaries (see CONTRACT.txt):
- Deterministic local domain logic only. No SNMP/OLT/SSH reads, no live
  monitoring polls, no device activation/suspension/termination, no message
  sending, no filesystem or network access. Live device actions are left
  ABSENT from HANDLERS until a verified adapter is bound by root.
- Monitoring handlers evaluate OPERATOR-SUPPLIED evidence (samples with
  timestamp+source); they compute freshness vs ctx.now and never fabricate
  polls or claim live state.
- Money is whole IDR via common.money (strict int). All coordinates are
  integer micro-degrees (1e-6 deg) to keep schemas strictly integer.
- Aggregations run over ctx.list with the 1000-record cap and are marked
  bounded analytics in results and LIMITATIONS.
"""

from .common import DomainError, require, identifier, money, now_iso, parse_time

ENGINES_OWNED = (1, 2, 3, 11, 12)

_SUBSCRIBER_STATUS = frozenset(("pending", "active", "suspended", "terminated"))
_DEVICE_TYPES = frozenset(("olt", "ont", "router", "odp", "odc", "switch"))
_DEVICE_STATUS = frozenset(("planned", "installed", "retired"))
_DEVICE_STATUS_ORDER = {"planned": 0, "installed": 1, "retired": 2}
_RESOURCE_TYPES = frozenset(("olt_port", "ip", "bandwidth", "ont"))
_RESOURCE_STATUS = frozenset(("available", "reserved", "assigned"))
_LINK_TYPES = frozenset(("fiber", "wireless"))
_PROV_STATUS = frozenset(("draft", "validated", "resources_reserved", "awaiting_connector", "active"))
_WORK_ORDER_STATUS = frozenset(("open", "assigned", "in_progress", "completed"))
_TICKET_STATUS = frozenset(("open", "assigned", "resolved"))
_SEVERITIES = ("P1", "P2", "P3")
_SEVERITY_RANK = {"P1": 0, "P2": 1, "P3": 2}

_LIST_CAP = 1000
_MAX_AGE_LIMIT_S = 30 * 24 * 3600


def _require_int(value, code, minimum=None, maximum=None):
    require(isinstance(value, int) and not isinstance(value, bool), code, 400)
    if minimum is not None:
        require(value >= minimum, code, 400)
    if maximum is not None:
        require(value <= maximum, code, 400)
    return value


def _int_arg(args, key, minimum=None, maximum=None, required=True, default=None):
    if key not in args or args[key] is None:
        require(not required, "invalid_argument", 400)
        return default
    return _require_int(args[key], "invalid_argument", minimum, maximum)


def _text(value, max_len, code="invalid_argument"):
    require(isinstance(value, str), code, 400)
    require(1 <= len(value) <= max_len, code, 400)
    require(all(ord(ch) >= 32 for ch in value), code, 400)
    return value


def _text_arg(args, key, max_len, required=True, default=None, code="invalid_argument"):
    if key not in args or args[key] is None:
        require(not required, code, 400)
        return default
    return _text(args[key], max_len, code)


def _enum_arg(args, key, allowed, required=True, default=None):
    if key not in args or args[key] is None:
        require(not required, "invalid_argument", 400)
        return default
    require(args[key] in allowed, "invalid_argument", 400)
    return args[key]


def _id_arg(ctx, args, key, required=True, default=None):
    if key not in args or args[key] is None:
        require(not required, "invalid_argument", 400)
        return default
    return identifier(args[key])


def _id_list_arg(args, key, max_items, required=True, default=None):
    if key not in args or args[key] is None:
        require(not required, "invalid_argument", 400)
        return default
    value = args[key]
    require(isinstance(value, list) and 1 <= len(value) <= max_items, "invalid_argument", 400)
    return [identifier(item) for item in value]


def _coord_arg(args, key, lo, hi, required=True, default=None):
    if key not in args or args[key] is None:
        require(not required, "invalid_argument", 400)
        return default
    return _require_int(args[key], "invalid_argument", lo, hi)


def _micro_lat(args, key="lat_micro", required=True, default=None):
    return _coord_arg(args, key, -90000000, 90000000, required, default)


def _micro_lon(args, key="lon_micro", required=True, default=None):
    return _coord_arg(args, key, -180000000, 180000000, required, default)


def _iso_arg(ctx, args, key, required=True, default=None, allow_future=False):
    if key not in args or args[key] is None:
        require(not required, "invalid_argument", 400)
        return default, None
    raw = args[key]
    require(isinstance(raw, str), "invalid_argument", 400)
    parsed = parse_time(raw)
    if not allow_future:
        require(parsed <= parse_time(ctx.now), "invalid_argument", 400)
    return raw, parsed


def _age_s(ctx, iso_str):
    return int((parse_time(ctx.now) - parse_time(iso_str)).total_seconds())


def _get_required(ctx, kind, record_id, code="not_found"):
    rec = ctx.get(kind, identifier(record_id))
    require(rec is not None, code, 404)
    return rec


def _haversine_m(lat1, lon1, lat2, lon2):
    import math

    rlat1 = lat1 * 1e-6 * math.pi / 180.0
    rlat2 = lat2 * 1e-6 * math.pi / 180.0
    dlat = (lat2 - lat1) * 1e-6 * math.pi / 180.0
    dlon = (lon2 - lon1) * 1e-6 * math.pi / 180.0
    a = math.sin(dlat / 2.0) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2
    return int(round(6371000.0 * 2.0 * math.asin(min(1.0, math.sqrt(a)))))


def _bounded(limit):
    return {"bounded": True, "limit": limit}


def _fresh_samples(ctx, samples, max_age_s):
    require(isinstance(samples, list) and len(samples) >= 1, "insufficient_evidence", 400)
    require(len(samples) <= _LIST_CAP, "invalid_argument", 400)
    now = parse_time(ctx.now)
    fresh = []
    stale_count = 0
    future_ignored = 0
    seen = set()
    duplicates_ignored = 0
    for item in samples:
        require(isinstance(item, dict), "invalid_argument", 400)
        source = identifier(item.get("source"))
        observed_raw = item.get("observed_at")
        require(isinstance(observed_raw, str), "invalid_argument", 400)
        observed = parse_time(observed_raw)
        key = (source, observed)
        if key in seen:
            duplicates_ignored += 1
            continue
        seen.add(key)
        age = (now - observed).total_seconds()
        if age < 0:
            future_ignored += 1
            continue
        if age > max_age_s:
            stale_count += 1
            continue
        entry = dict(item)
        entry["source"] = source
        entry["observed_at"] = observed_raw
        entry["age_s"] = age
        if entry.get("value_int") is not None:
            entry["value_int"] = _require_int(entry.get("value_int"), "invalid_argument")
        fresh.append(entry)
    fresh.sort(key=lambda s: parse_time(s["observed_at"]))
    return {
        "fresh": fresh,
        "stale_count": stale_count,
        "future_ignored": future_ignored,
        "duplicates_ignored": duplicates_ignored,
        "total": len(samples),
    }


def _metric_evaluation(ctx, args, metric, unit, lo, hi, direction, default_threshold):
    device_id = _id_arg(ctx, args, "device_id")
    max_age_s = _int_arg(args, "max_age_s", 1, _MAX_AGE_LIMIT_S, required=False, default=3600)
    threshold = _int_arg(
        args, "warn_threshold", lo, hi, required=False, default=default_threshold
    )
    report = _fresh_samples(ctx, args.get("samples"), max_age_s)
    fresh = report["fresh"]
    if fresh:
        latest = fresh[-1]
        value = latest["value_int"]
        breached = threshold is not None and (
            value >= threshold if direction == "high" else value <= threshold
        )
        status = "degraded" if breached else "ok"
    else:
        latest = None
        value = None
        breached = False
        status = "stale"
    return {
        "device_id": device_id,
        "metric": metric,
        "unit": unit,
        "direction": direction,
        "warn_threshold": threshold,
        "samples_total": report["total"],
        "fresh_count": len(fresh),
        "stale_count": report["stale_count"],
        "duplicates_ignored": report["duplicates_ignored"],
        "future_ignored": report["future_ignored"],
        "latest_fresh": latest,
        "breach": breached,
        "status": status,
        "evidence_based": True,
        "note": "operator-supplied evidence only; no live poll performed",
    }


def _merge_intervals(intervals):
    spans = sorted((s, e) for s, e in intervals if e > s)
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _ticket_get(ctx, ticket_id):
    ticket = _get_required(ctx, "ticket", ticket_id)
    require(ticket.get("category") in ("noc", "trouble"), "invalid_argument", 400)
    return ticket


def _record_ticket(ctx, category, args):
    title = _text_arg(args, "title", 200)
    detail = _text_arg(args, "detail", 2000, required=False)
    severity = _enum_arg(args, "severity", _SEVERITIES, required=False, default="P3")
    device_id = _id_arg(ctx, args, "device_id", required=False)
    subscriber_id = _id_arg(ctx, args, "subscriber_id", required=False)
    if device_id is not None:_get_required(ctx,'device',device_id)
    if subscriber_id is not None:
        sub = ctx.get("subscriber", subscriber_id)
        require(sub is not None and sub.get("status") != "terminated", "validation_failed", 400)
    return ctx.create(
        "ticket",
        {
            "category": category,
            "title": title,
            "detail": detail,
            "severity": severity,
            "device_id": device_id,
            "subscriber_id": subscriber_id,
            "status": "open",
            "assigned_to": None,
            "assigned_at": None,
            "opened_at": ctx.now,
            "resolved_at": None,
            "resolution": None,
            "history": [],
        },
    )


def _complete_work_order(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    wo = _get_required(ctx, "work_order", work_order_id)
    require(wo["status"] in ("assigned", "in_progress"), "invalid_transition", 400)
    note = _text_arg(args, "note", 2000)
    evidence_refs = _id_list_arg(args, "evidence_refs", 10, required=False, default=[])
    completed_at, _ = _iso_arg(ctx, args, "completed_at", required=False, default=None)
    signature_ref = _id_arg(ctx, args, "signature_ref", required=False)
    updated = ctx.update(
        "work_order",
        work_order_id,
        {
            "status": "completed",
            "completed_at": completed_at or ctx.now,
            "completion": {
                "note": note,
                "evidence_refs": evidence_refs,
                "signature_ref": signature_ref,
                "source": "operator_supplied",
            },
            "service_activation": "not_performed_requires_adapter",
        },
    )
    return updated


def _open_outage_for_asset(ctx, asset_kind, asset_id):
    for rec in ctx.list("outage", where={"asset_kind": asset_kind, "asset_id": asset_id}):
        if rec.get("status") == "open":
            return rec
    return None


def h_netinv_device_register(ctx, args):
    name = _text_arg(args, "name", 100)
    device_type = _enum_arg(args, "device_type", _DEVICE_TYPES)
    status = _enum_arg(args, "status", _DEVICE_STATUS, required=False, default="planned")
    site = _text_arg(args, "site", 100, required=False)
    lat = _micro_lat(args, required=False)
    lon = _micro_lon(args, required=False)
    parent_id = _id_arg(ctx, args, "parent_id", required=False)
    capacity_ports = _int_arg(args, "capacity_ports", 0, 4096, required=False)
    if parent_id is not None:
        require(ctx.get("device", parent_id) is not None, "not_found", 404)
    rec = ctx.create(
        "device",
        {
            "name": name,
            "device_type": device_type,
            "status": status,
            "site": site,
            "lat_micro": lat,
            "lon_micro": lon,
            "parent_id": parent_id,
            "capacity_ports": capacity_ports,
        },
    )
    return {"device_id": rec["id"], "status": rec["status"]}


def _active_links_guard(ctx,device_id):
    require(not any(device_id in (r.get('a_device_id'),r.get('b_device_id')) and r.get('status')!='inactive' for r in ctx.list('network_link',limit=_LIST_CAP)),'active_network_links',409)


def h_netinv_device_update(ctx, args):
    device_id = _id_arg(ctx, args, "device_id")
    device = _get_required(ctx, "device", device_id)
    changes = {}
    if "site" in args:
        changes["site"] = _text_arg(args, "site", 100, required=True)
    if "lat_micro" in args:
        changes["lat_micro"] = _micro_lat(args)
    if "lon_micro" in args:
        changes["lon_micro"] = _micro_lon(args)
    if "capacity_ports" in args:
        changes["capacity_ports"] = _int_arg(args, "capacity_ports", 0, 4096)
    if "parent_id" in args:
        parent_id = _id_arg(ctx, args, "parent_id")
        require(parent_id != device_id, "validation_failed", 400)
        if parent_id is not None:
            parent = ctx.get("device", parent_id)
            require(parent is not None, "not_found", 404)
            walk = parent.get("parent_id")
            hops = 0
            while walk is not None and hops <= _LIST_CAP:
                require(walk != device_id, "circular_hierarchy", 400)
                walk = ctx.get("device", walk).get("parent_id")
                hops += 1
        changes["parent_id"] = parent_id
    if "status" in args:
        new_status = _enum_arg(args, "status", _DEVICE_STATUS)
        old_rank = _DEVICE_STATUS_ORDER[device["status"]]
        require(_DEVICE_STATUS_ORDER[new_status] >= old_rank, "invalid_transition", 400)
        if new_status=='retired':
            _active_links_guard(ctx,device_id)
            resources=ctx.list('resource',where={'device_id':device_id},limit=_LIST_CAP)
            require(not any(r.get('status') in ('assigned','reserved') for r in resources),'resources_still_assigned',409)
            require(not any(d.get('parent_id')==device_id and d.get('status')!='retired' for d in ctx.list('device',limit=_LIST_CAP)),'active_child_devices',409)
        changes["status"] = new_status
    if 'capacity_ports' in changes:
        require(changes['capacity_ports']>=len(ctx.list('resource',where={'device_id':device_id,'resource_type':'olt_port'},limit=_LIST_CAP)),'port_capacity_exceeded',409)
    require(bool(changes), "invalid_argument", 400)
    updated = ctx.update("device", device_id, changes)
    return {"device_id": device_id, "status": updated["status"], "changes": sorted(changes.keys())}


def h_netinv_device_decommission(ctx, args):
    device_id = _id_arg(ctx, args, "device_id")
    device = _get_required(ctx, "device", device_id)
    reason = _text_arg(args, "reason", 500, required=False)
    assigned = [
        r
        for r in ctx.list("resource", where={"device_id": device_id}, limit=_LIST_CAP)
        if r.get("status") in ("assigned","reserved")
    ]
    require(not assigned, "resources_still_assigned", 409)
    children = [
        d
        for d in ctx.list("device", limit=_LIST_CAP)
        if d.get("parent_id") == device_id and d.get("status") != "retired"
    ]
    require(not children, "validation_failed", 409)
    _active_links_guard(ctx,device_id)
    updated = ctx.update("device", device_id, {"status": "retired", "retire_reason": reason})
    return {"device_id": device_id, "status": updated["status"]}


def h_netinv_hierarchy_build(ctx, args):
    root_device_id = _id_arg(ctx, args, "root_device_id", required=False)
    devices = ctx.list("device", limit=_LIST_CAP)
    by_id = {d["id"]: d for d in devices}
    depth = {}
    children = {d["id"]: [] for d in devices}

    def walk(device_id, stack):
        chain=[];seen=set();current=device_id
        while current in by_id and current not in depth:
            if current in seen:
                for item in chain:depth[item]=-1
                return -1
            seen.add(current);chain.append(current);current=by_id[current].get('parent_id')
        value=depth.get(current,-1) if current in depth else -1
        cycle=current in depth and depth[current]==-1
        for item in reversed(chain):
            value=-1 if cycle else value+1;depth[item]=value
        return depth[device_id]

    for d in devices:
        walk(d["id"], frozenset())
        parent = d.get("parent_id")
        if parent in by_id and depth.get(d["id"], -1) >= 0:
            children[parent].append(d["id"])

    nodes = []
    for d in devices:
        nodes.append(
            {
                "device_id": d["id"],
                "name": d["name"],
                "device_type": d["device_type"],
                "status": d["status"],
                "parent_id": d.get("parent_id"),
                "depth": depth.get(d["id"], -1),
                "cycle": depth.get(d["id"], -1) == -1,
            }
        )
    if root_device_id is not None:
        require(root_device_id in by_id, "not_found", 404)
        nodes = [n for n in nodes if n["device_id"] == root_device_id or _under(n, root_device_id, children)]
    return {"nodes": nodes, "count": len(nodes), "analytics": _bounded(_LIST_CAP)}


def _under(node, root_device_id, children):
    stack = [root_device_id]
    while stack:
        current = stack.pop()
        if current == node["device_id"]:
            return True
        stack.extend(children.get(current, []))
    return False


def h_netinv_port_management(ctx, args):
    device_id = _id_arg(ctx, args, "device_id")
    device = _get_required(ctx, "device", device_id)
    require(device.get('status')!='retired','device_retired',409)
    ports = args.get("ports")
    existing = ctx.list("resource", where={"resource_type": "olt_port", "device_id": device_id}, limit=_LIST_CAP)
    existing_codes = {r.get("port_code",r["value"].split(":", 1)[-1]) for r in existing}
    if ports is None:
        return {"device_id": device_id, "ports": existing, "count": len(existing)}
    require(isinstance(ports, list) and 1 <= len(ports) <= 500, "invalid_argument", 400)
    if device.get('capacity_ports') is not None:
        require(len(existing)+len(ports)<=device['capacity_ports'],'port_capacity_exceeded',409)
    created = []
    for port in ports:
        require(isinstance(port, dict), "invalid_argument", 400)
        code = _text(port.get("port_code"), 50)
        require(code not in existing_codes, "duplicate", 409)
        existing_codes.add(code)
        rec = ctx.create(
            "resource",
            {
                "resource_type": "olt_port",
                "value": "%s:%s" % (device_id, code),
                "device_id": device_id,
                "port_code": code,
                "status": "available",
                "provision_order_id": None,
                "subscriber_id": None,
            },
        )
        created.append(rec)
    return {"device_id": device_id, "created": [r["id"] for r in created], "count": len(created)}


def h_netinv_link_management(ctx, args):
    if args.get('link_id'):
        link=_get_required(ctx,'network_link',identifier(args['link_id']))
        require(not args.get('a_device_id') and not args.get('b_device_id'),'ambiguous_link_action')
        status=_enum_arg(args,'status',frozenset(('active','planned','inactive')))
        if status!='inactive':
            for id in (link['a_device_id'],link['b_device_id']):require(_get_required(ctx,'device',id).get('status')!='retired','device_retired',409)
        return {'link':ctx.update('network_link',link['id'],{'status':status}),'network_configuration_changed':False}
    a_device_id = _id_arg(ctx, args, "a_device_id", required=False)
    b_device_id = _id_arg(ctx, args, "b_device_id", required=False)
    if a_device_id is None and b_device_id is None:
        links = ctx.list("network_link", limit=_LIST_CAP)
        return {"links": links, "count": len(links), "analytics": _bounded(_LIST_CAP)}
    if not (a_device_id is not None and b_device_id is not None):
        links = ctx.list("network_link", limit=_LIST_CAP)
        side = a_device_id or b_device_id
        links = [l for l in links if side in (l.get("a_device_id"), l.get("b_device_id"))]
        return {"links": links, "count": len(links), "analytics": _bounded(_LIST_CAP)}
    require(a_device_id != b_device_id, "validation_failed", 400)
    for id in (a_device_id,b_device_id):require(_get_required(ctx,'device',id).get('status')!='retired','device_retired',409)
    link_type = _enum_arg(args, "link_type", _LINK_TYPES, required=False, default="fiber")
    length_m = _int_arg(args, "length_m", 0, 500000, required=False)
    status = _enum_arg(args, "status", frozenset(("planned", "active")), required=False, default="active")
    for link in ctx.list("network_link", limit=_LIST_CAP):
        if {link.get("a_device_id"), link.get("b_device_id")} == {a_device_id, b_device_id}:
            require(False, "duplicate", 409)
    rec = ctx.create(
        "network_link",
        {
            "a_device_id": a_device_id,
            "b_device_id": b_device_id,
            "link_type": link_type,
            "length_m": length_m,
            "status": status,
        },
    )
    return {"link_id": rec["id"], "status": rec["status"]}


def h_netinv_cable_trace(ctx, args):
    from_device_id = _id_arg(ctx, args, "from_device_id")
    to_device_id = _id_arg(ctx, args, "to_device_id")
    require(from_device_id != to_device_id, "invalid_argument", 400)
    _get_required(ctx, "device", from_device_id)
    _get_required(ctx, "device", to_device_id)
    links = ctx.list("network_link", where={"status": "active"}, limit=_LIST_CAP)
    adjacency = {}
    for link in links:
        adjacency.setdefault(link["a_device_id"], []).append(link)
        adjacency.setdefault(link["b_device_id"], []).append(link)
    from collections import deque

    queue = deque([from_device_id])
    prev = {from_device_id: None}
    while queue:
        current = queue.popleft()
        for link in adjacency.get(current, []):
            nxt = link["b_device_id"] if link["a_device_id"] == current else link["a_device_id"]
            if nxt not in prev:
                prev[nxt] = (current, link)
                queue.append(nxt)
    if to_device_id not in prev:
        return {"reachable": False, "path_device_ids": [], "link_ids": [], "total_length_m": None}
    path = [to_device_id]
    link_ids = []
    total = 0
    cursor = to_device_id
    while prev[cursor] is not None:
        parent, link = prev[cursor]
        link_ids.append(link["id"])
        total += link.get("length_m") or 0
        path.append(parent)
        cursor = parent
    path.reverse()
    link_ids.reverse()
    return {
        "reachable": True,
        "path_device_ids": path,
        "link_ids": link_ids,
        "total_length_m": total,
        "hops": len(link_ids),
    }


def h_netinv_spatial_view(ctx, args):
    center_lat = _micro_lat(args, "center_lat_micro")
    center_lon = _micro_lon(args, "center_lon_micro")
    radius_m = _int_arg(args, "radius_m", 1, 100000)
    results = []
    for device in ctx.list("device", limit=_LIST_CAP):
        if device.get("lat_micro") is None or device.get("lon_micro") is None:
            continue
        distance = _haversine_m(center_lat, center_lon, device["lat_micro"], device["lon_micro"])
        if distance <= radius_m:
            results.append({"device_id": device["id"], "name": device["name"], "distance_m": distance})
    results.sort(key=lambda r: (r["distance_m"], r["device_id"]))
    return {
        "devices": results,
        "count": len(results),
        "note": "proximity listing from inventory coordinates only; does not promise service coverage",
        "analytics": _bounded(_LIST_CAP),
    }


def h_netinv_capacity_track(ctx, args):
    device_id = _id_arg(ctx, args, "device_id", required=False)
    where = {"resource_type": "olt_port"}
    if device_id is not None:
        where["device_id"] = device_id
        _get_required(ctx, "device", device_id)
    resources = ctx.list("resource", where=where, limit=_LIST_CAP)
    per_device = {}
    for res in resources:
        entry = per_device.setdefault(res["device_id"], {"total": 0, "available": 0, "reserved": 0, "assigned": 0})
        entry["total"] += 1
        entry[res["status"]] += 1
    devices = []
    for dev_id in sorted(per_device):
        entry = per_device[dev_id]
        utilization_bps = entry["assigned"] * 10000 // entry["total"] if entry["total"] else 0
        devices.append(dict(entry, device_id=dev_id, utilization_bps=utilization_bps))
    return {"devices": devices, "count": len(devices), "analytics": _bounded(_LIST_CAP)}


def h_netinv_utilization_track(ctx, args):
    device_id = _id_arg(ctx, args, "device_id")
    _get_required(ctx, "device", device_id)
    max_age_s = _int_arg(args, "max_age_s", 1, _MAX_AGE_LIMIT_S, required=False, default=3600)
    interfaces = args.get("interfaces")
    require(isinstance(interfaces, list) and 1 <= len(interfaces) <= 200, "insufficient_evidence", 400)
    report = _fresh_samples(ctx, interfaces, max_age_s)
    ranked = sorted(report["fresh"], key=lambda s: (-s["value_int"], s["source"]))
    device_avg_bps = (sum(s["value_int"] for s in report["fresh"]) // len(report["fresh"])) if report["fresh"] else None
    return {
        "device_id": device_id,
        "unit": "utilization_bps (0..10000)",
        "interfaces_fresh": ranked,
        "device_avg_bps": device_avg_bps,
        "stale_count": report["stale_count"],
        "duplicates_ignored": report["duplicates_ignored"],
        "future_ignored": report["future_ignored"],
        "evidence_based": True,
        "analytics": _bounded(200),
    }


def h_netinv_olt_port_alloc(ctx, args):
    resource_id=_id_arg(ctx,args,'resource_id');subscriber_id=_id_arg(ctx,args,'subscriber_id')
    resource=_get_required(ctx,'resource',resource_id);require(resource['resource_type']=='olt_port','validation_failed')
    sub=_get_required(ctx,'subscriber',subscriber_id);require(sub.get('status')!='terminated','validation_failed')
    order_id=args.get('provision_order_id');action=args.get('action','assign');order=None
    if order_id:
        order=_get_required(ctx,'provision_order',identifier(order_id));require(order['subscriber_id']==subscriber_id,'subscriber_mismatch',409)
    if resource.get('provision_order_id'):
        require(order_id==resource['provision_order_id'],'reservation_owner_mismatch',409)
    if resource.get('subscriber_id'):require(resource['subscriber_id']==subscriber_id,'subscriber_mismatch',409)
    if action=='release':
        require(resource['status'] in ('reserved','assigned'),'invalid_transition',409)
        updated=ctx.update('resource',resource_id,{'status':'available','subscriber_id':None,'provision_order_id':None})
        if order:
            reserved=[x for x in order.get('reserved_resource_ids',[]) if x!=resource_id];assigned=[x for x in order.get('assigned_resource_ids',[]) if x!=resource_id]
            ctx.update('provision_order',order_id,{'reserved_resource_ids':reserved,'assigned_resource_ids':assigned,'status':'resources_reserved' if reserved else 'draft'})
    else:
        require(action=='assign','invalid_argument');require(resource['status'] in ('available','reserved'),'conflict',409)
        if resource.get('device_id'):
            require(_get_required(ctx,'device',resource['device_id']).get('status')!='retired','device_retired',409)
        if order:require(order['status'] in ('draft','validated','resources_reserved'),'invalid_transition')
        updated=ctx.update('resource',resource_id,{'status':'assigned','subscriber_id':subscriber_id,'provision_order_id':order_id})
        if order:
            reserved=[x for x in order.get('reserved_resource_ids',[]) if x!=resource_id];assigned=list(dict.fromkeys(order.get('assigned_resource_ids',[])+[resource_id]))
            ctx.update('provision_order',order_id,{'reserved_resource_ids':reserved,'assigned_resource_ids':assigned,'status':'resources_reserved' if reserved else 'awaiting_connector'})
    return {'resource_id':resource_id,'status':updated['status'],'subscriber_id':updated.get('subscriber_id'),'local_inventory_only':True,'network_configuration_changed':False}


def h_netinv_spare_parts(ctx, args):
    sku = _id_arg(ctx, args, "sku", required=False)
    if sku is None:
        parts = ctx.list("spare_part", limit=_LIST_CAP)
        return {"parts": parts, "count": len(parts), "analytics": _bounded(_LIST_CAP)}
    existing = ctx.list("spare_part", where={"sku": sku}, limit=_LIST_CAP)
    if not existing:
        name = _text_arg(args, "name", 100)
        qty = _int_arg(args, "qty", 0, 1000000)
        rec = ctx.create("spare_part", {"sku": sku, "name": name, "qty": qty, "min_qty": 0})
        return {"part": rec, "action": "created"}
    part = existing[0]
    adjust_delta = _int_arg(args, "adjust_delta", -1000000, 1000000, required=False)
    if adjust_delta is None:
        return {"part": part, "action": "unchanged"}
    new_qty = part["qty"] + adjust_delta
    require(new_qty >= 0, "validation_failed", 400)
    updated = ctx.update("spare_part", part["id"], {"qty": new_qty})
    return {"part": updated, "action": "adjusted", "adjust_delta": adjust_delta}


def h_prov_order_create(ctx, args):
    subscriber_id = _id_arg(ctx, args, "subscriber_id")
    product_id = _id_arg(ctx, args, "product_id", required=False)
    sub = ctx.get("subscriber", subscriber_id)
    require(sub is not None and sub.get("status") != "terminated", "validation_failed", 400)
    if product_id is not None:
        product = ctx.get("product", product_id)
        require(product is not None and product.get("active") is True, "validation_failed", 400)
    required = []
    raw_required = args.get("required_resources")
    if raw_required is not None:
        require(isinstance(raw_required, list) and 1 <= len(raw_required) <= 10, "invalid_argument", 400)
        for item in raw_required:
            require(isinstance(item, dict), "invalid_argument", 400)
            rtype = item.get("resource_type")
            require(rtype in _RESOURCE_TYPES, "invalid_argument", 400)
            count = _require_int(item.get("count"), "invalid_argument", 1, 1000)
            required.append({"resource_type": rtype, "count": count})
    rec = ctx.create(
        "provision_order",
        {
            "subscriber_id": subscriber_id,
            "product_id": product_id,
            "status": "draft",
            "required_resources": required,
            "reserved_resource_ids": [],
        },
    )
    return {"provision_order_id": rec["id"], "status": rec["status"]}


def h_prov_order_validate(ctx, args):
    order_id = _id_arg(ctx, args, "provision_order_id")
    order = _get_required(ctx, "provision_order", order_id)
    require(order["status"] in ("draft", "validated"), "invalid_transition", 400)
    checks = []
    sub = ctx.get("subscriber", order["subscriber_id"])
    sub_ok = sub is not None and sub.get("status") in ("pending", "active")
    checks.append({"check": "subscriber_provisionable", "ok": sub_ok, "detail": sub.get("status") if sub else "missing"})
    prod_ok = True
    if order.get("product_id"):
        product = ctx.get("product", order["product_id"])
        prod_ok = product is not None and product.get("active") is True
        checks.append({"check": "product_active", "ok": prod_ok, "detail": None if prod_ok else "inactive_or_missing"})
    availability = {}
    for req in order.get("required_resources", []):
        avail = len(
            ctx.list(
                "resource",
                where={"resource_type": req["resource_type"], "status": "available"},
                limit=_LIST_CAP,
            )
        )
        availability[req["resource_type"]] = {"available": avail, "required": req["count"], "ok": avail >= req["count"]}
        checks.append(
            {
                "check": "resource_availability:%s" % req["resource_type"],
                "ok": avail >= req["count"],
                "detail": "%d/%d" % (avail, req["count"]),
            }
        )
    valid = all(c["ok"] for c in checks)
    require(valid, "validation_failed", 400)
    updated = ctx.update("provision_order", order_id, {"status": "validated"})
    return {
        "provision_order_id": order_id,
        "status": updated["status"],
        "checks": checks,
        "availability": availability,
    }


def h_prov_allocate_resource(ctx, args):
    order_id = _id_arg(ctx, args, "provision_order_id")
    order = _get_required(ctx, "provision_order", order_id)
    require(order["status"] in ("draft", "validated", "resources_reserved"), "invalid_transition", 400)
    resource_type = args.get("resource_type")
    require(resource_type in _RESOURCE_TYPES, "invalid_argument", 400)
    count = _int_arg(args, "count", 1, 1000)
    if order.get('required_resources'):
        allowed=sum(x['count'] for x in order['required_resources'] if x['resource_type']==resource_type)
        require(0<count<=allowed,'resource_requirement_exceeded',409)
    already = [rid for rid in order.get("reserved_resource_ids", [])]
    allocated_same_type = [
        rid
        for rid in already
        if ctx.get("resource", rid).get("resource_type") == resource_type
    ]
    if len(allocated_same_type) >= count:
        return {
            "provision_order_id": order_id,
            "status": order["status"],
            "allocated_resource_ids": allocated_same_type[:count],
            "note": "requirement already satisfied",
        }
    needed = count - len(allocated_same_type)
    available = sorted(
        ctx.list("resource", where={"resource_type": resource_type, "status": "available"}, limit=_LIST_CAP),
        key=lambda r: (r["value"], r["id"]),
    )
    require(len(available) >= needed, "insufficient_resources", 409)
    picked = available[:needed]
    picked_ids = []
    for res in picked:
        ctx.update("resource", res["id"], {"status": "reserved", "provision_order_id": order_id})
        picked_ids.append(res["id"])
    updated = ctx.update(
        "provision_order",
        order_id,
        {
            "status": "resources_reserved",
            "reserved_resource_ids": already + picked_ids,
        },
    )
    return {
        "provision_order_id": order_id,
        "status": updated["status"],
        "allocated_resource_ids": picked_ids,
    }


def h_prov_workorder_create(ctx, args):
    subscriber_id = _id_arg(ctx, args, "subscriber_id")
    provision_order_id = _id_arg(ctx, args, "provision_order_id", required=False)
    technician_id = _id_arg(ctx, args, "technician_id", required=False)
    title = _text_arg(args, "title", 200, required=False)
    priority = _enum_arg(args, "priority", frozenset(("emergency", "high", "normal")), required=False, default="normal")
    sub = ctx.get("subscriber", subscriber_id)
    require(sub is not None and sub.get("status") != "terminated", "validation_failed", 400)
    if provision_order_id is not None:
        order=_get_required(ctx, "provision_order", provision_order_id)
        require(order['subscriber_id']==subscriber_id,'subscriber_mismatch',409)
    status = "assigned" if technician_id is not None else "open"
    rec = ctx.create(
        "work_order",
        {
            "subscriber_id": subscriber_id,
            "provision_order_id": provision_order_id,
            "technician_id": technician_id,
            "title": title,
            "priority": priority,
            "status": status,
            "assigned_at": ctx.now if technician_id else None,
            "arrival": None,
            "completion": None,
            "completed_at": None,
            "reassignments": [],
            "source_outage_id": None,
            "required_skills": [],
        },
    )
    return {"work_order_id": rec["id"], "status": rec["status"]}


def h_prov_workorder_complete(ctx, args):
    updated = _complete_work_order(ctx, args)
    return {
        "work_order_id": updated["id"],
        "status": updated["status"],
        "service_activation": updated["service_activation"],
    }


def h_noc_monitor_uptime(ctx, args):
    device_id = _id_arg(ctx, args, "device_id")
    max_age_s = _int_arg(args, "max_age_s", 1, _MAX_AGE_LIMIT_S, required=False, default=3600)
    target_bps = _int_arg(args, "target_uptime_bps", 1, 10000, required=False, default=9900)
    report = _fresh_samples(ctx, args.get("samples"), max_age_s)
    fresh = report["fresh"]
    for sample in fresh:
        require(sample["value_int"] in (0, 1), "invalid_argument", 400)
    if fresh:
        up_count = sum(s["value_int"] for s in fresh)
        uptime_bps = up_count * 10000 // len(fresh)
        latest_state = "up" if fresh[-1]["value_int"] == 1 else "down"
        status = "ok" if uptime_bps >= target_bps else "degraded"
    else:
        uptime_bps = None
        latest_state = None
        status = "stale"
    return {
        "device_id": device_id,
        "metric": "up",
        "samples_total": report["total"],
        "fresh_count": len(fresh),
        "stale_count": report["stale_count"],
        "duplicates_ignored": report["duplicates_ignored"],
        "future_ignored": report["future_ignored"],
        "uptime_bps": uptime_bps,
        "target_uptime_bps": target_bps,
        "latest_state": latest_state,
        "status": status,
        "evidence_based": True,
        "note": "operator-supplied evidence only; no ICMP/SNMP poll performed",
    }


def h_noc_monitor_bandwidth(ctx, args):
    return _metric_evaluation(ctx, args, "bandwidth_mbps", "mbps", 0, 100000000, "high", None)


def h_noc_monitor_optical(ctx, args):
    return _metric_evaluation(ctx, args, "optical_power", "dbm_hundredths", -8000, 0, "low", -2700)


def h_noc_monitor_temperature(ctx, args):
    return _metric_evaluation(ctx, args, "temperature_c", "celsius", -40, 100, "high", 60)


def h_noc_alert_threshold(ctx, args):
    require(args.get("warn_threshold") is not None, "invalid_argument", 400)
    evaluation = _metric_evaluation(ctx, args, args.get("metric_name") or "custom", "int", -10**12, 10**12, "high", None)
    result = {
        "device_id": evaluation["device_id"],
        "metric": evaluation["metric"],
        "status": evaluation["status"],
        "breach": evaluation["breach"],
        "fresh_count": evaluation["fresh_count"],
        "stale_count": evaluation["stale_count"],
        "duplicates_ignored": evaluation["duplicates_ignored"],
    }
    if not evaluation["breach"] or evaluation["latest_fresh"] is None:
        result["alert_id"] = None
        return result
    existing_alerts = ctx.list(
        "alert", where={"device_id": evaluation["device_id"], "type": "threshold_breach"}, limit=_LIST_CAP
    )
    latest = evaluation["latest_fresh"]
    for alert in existing_alerts:
        if alert.get("metric") == evaluation["metric"] and parse_time(alert["observed_at"]) == parse_time(latest["observed_at"]) and alert.get("source") == latest["source"]:
            result["alert_id"] = alert["id"]
            result["note"] = "duplicate evidence; existing alert returned"
            return result
    rec = ctx.create(
        "alert",
        {
            "type": "threshold_breach",
            "device_id": evaluation["device_id"],
            "metric": evaluation["metric"],
            "severity": "warning",
            "value_int": latest["value_int"],
            "observed_at": latest["observed_at"],
            "source": latest["source"],
        },
    )
    ctx.event("noc.alert.threshold", {"alert_id": rec["id"], "device_id": evaluation["device_id"]})
    result["alert_id"] = rec["id"]
    return result


def h_noc_alert_fiber_cut(ctx, args):
    device_id = _id_arg(ctx, args, "device_id")
    _get_required(ctx, "device", device_id)
    observed_at, _ = _iso_arg(ctx, args, "observed_at")
    source = identifier(args.get("source"))
    detail = _text_arg(args, "detail", 1000, required=False)
    max_age_s = _int_arg(args, "max_age_s", 1, _MAX_AGE_LIMIT_S, required=False, default=3600)
    age = _age_s(ctx, observed_at)
    require(age <= max_age_s, "stale_evidence", 400)
    existing = _open_outage_for_asset(ctx, "device", device_id)
    if existing is not None:
        return {
            "outage_id": existing["id"],
            "status": existing["status"],
            "note": "open outage already recorded for asset",
            "duplicate": True,
        }
    alert = ctx.create(
        "alert",
        {
            "type": "fiber_cut_los",
            "device_id": device_id,
            "severity": "critical",
            "observed_at": observed_at,
            "source": source,
            "detail": detail,
        },
    )
    outage = ctx.create(
        "outage",
        {
            "asset_kind": "device",
            "asset_id": device_id,
            "status": "open",
            "severity": "P1",
            "cause": "fiber_cut_los",
            "started_at": observed_at,
            "ended_at": None,
            "alert_id": alert["id"],
            "correlation": None,
        },
    )
    ctx.event("noc.alert.fiber.cut", {"outage_id": outage["id"], "device_id": device_id})
    return {"alert_id": alert["id"], "outage_id": outage["id"], "status": outage["status"], "duplicate": False}


def h_noc_alert_outage(ctx, args):
    asset_id = _id_arg(ctx, args, "asset_id")
    asset_kind = _enum_arg(args, "asset_kind", frozenset(("device", "resource")), required=False, default="device")
    _get_required(ctx, asset_kind, asset_id)
    max_age_s = _int_arg(args, "max_age_s", 1, _MAX_AGE_LIMIT_S, required=False, default=3600)
    min_subscribers = _int_arg(args, "min_subscribers", 2, 1000, required=False, default=3)
    window_minutes = _int_arg(args, "window_minutes", 1, 1440, required=False, default=60)
    observations = args.get("observations")
    require(isinstance(observations, list) and 1 <= len(observations) <= 1000, "insufficient_evidence", 400)
    now = parse_time(ctx.now)
    distinct = {}
    duplicates_ignored = 0
    seen_keys = set()
    for obs in observations:
        require(isinstance(obs, dict), "invalid_argument", 400)
        subscriber_id = identifier(obs.get("subscriber_id"))
        observed_raw = obs.get("observed_at")
        require(isinstance(observed_raw, str), "invalid_argument", 400)
        observed = parse_time(observed_raw)
        source = identifier(obs.get("source"))
        key = (subscriber_id, observed, source)
        if key in seen_keys:
            duplicates_ignored += 1
            continue
        seen_keys.add(key)
        age = (now - observed).total_seconds()
        if age < 0 or age > max_age_s:
            continue
        if subscriber_id in distinct and parse_time(distinct[subscriber_id]['observed_at']) >= observed:
            continue
        distinct[subscriber_id] = {"subscriber_id": subscriber_id, "observed_at": observed_raw, "source": source}
    recent = [
        obs
        for obs in distinct.values()
        if abs(int((now - parse_time(obs["observed_at"])).total_seconds())) <= window_minutes * 60
    ]
    correlated = len(recent) >= min_subscribers
    existing = _open_outage_for_asset(ctx, asset_kind, asset_id)
    result = {
        "asset_id": asset_id,
        "asset_kind": asset_kind,
        "distinct_subscribers": len(recent),
        "observations_total": len(observations),
        "duplicates_ignored": duplicates_ignored,
        "out_of_window_or_stale_ignored": len(distinct) - len(recent),
        "min_subscribers": min_subscribers,
        "window_minutes": window_minutes,
        "correlation_rule": "independent subscriber reports on the same asset within window",
    }
    if not correlated:
        result["declared"] = False
        result["reason"] = "insufficient_independent_observations"
        result["outage_id"] = existing["id"] if existing else None
        return result
    if existing is not None:
        result["declared"] = True
        result["outage_id"] = existing["id"]
        result["duplicate"] = True
        return result
    outage = ctx.create(
        "outage",
        {
            "asset_kind": asset_kind,
            "asset_id": asset_id,
            "status": "open",
            "severity": "P1",
            "cause": "mass_outage_correlation",
            "started_at": min((obs["observed_at"] for obs in recent),key=parse_time),
            "ended_at": None,
            "alert_id": None,
            "correlation": {"distinct_subscribers": sorted(recent, key=lambda o: parse_time(o["observed_at"]))},
        },
    )
    ctx.event("noc.alert.outage", {"outage_id": outage["id"], "asset_id": asset_id})
    result["declared"] = True
    result["outage_id"] = outage["id"]
    result["duplicate"] = False
    return result


def h_noc_ticket_create(ctx, args):
    rec = _record_ticket(ctx, "noc", args)
    return {"ticket_id": rec["id"], "status": rec["status"], "severity": rec["severity"]}


def h_noc_ticket_assign(ctx, args):
    ticket_id = _id_arg(ctx, args, "ticket_id")
    ticket = _ticket_get(ctx, ticket_id)
    assignee = _id_arg(ctx, args, "assignee")
    require(ticket["status"] in ("open", "assigned"), "invalid_transition", 400)
    updated = ctx.update(
        "ticket",
        ticket_id,
        {"status": "assigned", "assigned_to": assignee, "assigned_at": ctx.now},
    )
    return {"ticket_id": ticket_id, "status": updated["status"], "assigned_to": assignee}


def h_noc_escalation_matrix(ctx, args):
    return {
        "matrix": {
            "P1": {
                "response_minutes": 15,
                "resolution_minutes": 240,
                "escalation_chain": ["noc_shift_lead", "noc_manager", "director_operations"],
            },
            "P2": {
                "response_minutes": 60,
                "resolution_minutes": 1440,
                "escalation_chain": ["noc_shift_lead", "noc_manager"],
            },
            "P3": {
                "response_minutes": 240,
                "resolution_minutes": 4320,
                "escalation_chain": ["noc_shift_lead"],
            },
        },
        "note": "default reference matrix; override per contract via sla.define",
    }


def h_noc_dashboard_realtime(ctx, args):
    now = parse_time(ctx.now)
    tickets = ctx.list("ticket", limit=_LIST_CAP)
    open_by_severity = {sev: 0 for sev in _SEVERITIES}
    for ticket in tickets:
        if ticket.get("status") in ("open", "assigned"):
            open_by_severity[ticket.get("severity", "P3")] += 1
    outages = [o for o in ctx.list("outage", limit=_LIST_CAP) if o.get("status") == "open"]
    alerts = ctx.list("alert", limit=_LIST_CAP)
    alerts_24h = 0
    for alert in alerts:
        created = parse_time(alert.get("created_at"))
        if 0 <= int((now - created).total_seconds()) <= 86400:
            alerts_24h += 1
    devices_installed = len(
        [d for d in ctx.list("device", limit=_LIST_CAP) if d.get("status") == "installed"]
    )
    return {
        "as_at": ctx.now,
        "tickets_open_by_severity": open_by_severity,
        "outages_open": len(outages),
        "alerts_last_24h": alerts_24h,
        "devices_installed": devices_installed,
        "note": "snapshot of stored tenant records; not a live poll; bounded analytics",
        "analytics": _bounded(_LIST_CAP),
    }


def h_field_dispatch_create(ctx, args):
    subscriber_id = _id_arg(ctx, args, "subscriber_id")
    provision_order_id = _id_arg(ctx, args, "provision_order_id", required=False)
    title = _text_arg(args, "title", 200, required=False)
    priority = _enum_arg(args, "priority", frozenset(("emergency", "high", "normal")), required=False, default="normal")
    window_start, _ = _iso_arg(ctx, args, "window_start", required=False)
    window_end, _ = _iso_arg(ctx, args, "window_end", required=False)
    if window_start is not None and window_end is not None:
        require(parse_time(window_start) < parse_time(window_end), "invalid_argument", 400)
    required_skills = _text_list_arg(args, "required_skills", 20)
    site_lat = _micro_lat(args, "site_lat_micro", required=False)
    site_lon = _micro_lon(args, "site_lon_micro", required=False)
    sub = ctx.get("subscriber", subscriber_id)
    require(sub is not None and sub.get("status") != "terminated", "validation_failed", 400)
    if provision_order_id is not None:
        _get_required(ctx, "provision_order", provision_order_id)
    rec = ctx.create(
        "work_order",
        {
            "subscriber_id": subscriber_id,
            "provision_order_id": provision_order_id,
            "technician_id": None,
            "title": title,
            "priority": priority,
            "status": "open",
            "assigned_at": None,
            "arrival": None,
            "completion": None,
            "completed_at": None,
            "reassignments": [],
            "source_outage_id": None,
            "required_skills": required_skills,
            "window_start": window_start,
            "window_end": window_end,
            "site_lat_micro": site_lat,
            "site_lon_micro": site_lon,
        },
    )
    return {"work_order_id": rec["id"], "status": rec["status"], "priority": rec["priority"]}


def _text_list_arg(args, key, max_items, required=False, default=None):
    if key not in args or args[key] is None:
        require(not required, "invalid_argument", 400)
        return default
    value = args[key]
    require(isinstance(value, list) and len(value) <= max_items, "invalid_argument", 400)
    return [_text(item, 50) for item in value]


def h_field_dispatch_assign(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    wo = _get_required(ctx, "work_order", work_order_id)
    require(wo["status"] == "open", "invalid_transition", 400)
    technician_id = _id_arg(ctx, args, "technician_id", required=False)
    method = "explicit"
    if technician_id is None:
        candidates = args.get("candidates")
        require(isinstance(candidates, list) and 1 <= len(candidates) <= 200, "invalid_argument", 400)
        require(wo.get("site_lat_micro") is not None and wo.get("site_lon_micro") is not None, "validation_failed", 400)
        best = None
        for cand in candidates:
            require(isinstance(cand, dict), "invalid_argument", 400)
            cand_id = identifier(cand.get("technician_id"))
            cand_lat = _require_int(cand.get("lat_micro"), "invalid_argument", -90000000, 90000000)
            cand_lon = _require_int(cand.get("lon_micro"), "invalid_argument", -180000000, 180000000)
            distance = _haversine_m(wo["site_lat_micro"], wo["site_lon_micro"], cand_lat, cand_lon)
            if best is None or (distance, cand_id) < (best[0], best[1]):
                best = (distance, cand_id)
        technician_id = best[1]
        method = "nearest_heuristic"
    updated = ctx.update(
        "work_order",
        work_order_id,
        {"status": "assigned", "technician_id": technician_id, "assigned_at": ctx.now, "assign_method": method},
    )
    return {
        "work_order_id": work_order_id,
        "status": updated["status"],
        "technician_id": technician_id,
        "assign_method": method,
        "note": (
            "nearest heuristic uses straight-line distance only; no traffic or schedule data"
            if method == "nearest_heuristic"
            else "explicit assignment"
        ),
    }


def h_field_route_optimize(ctx, args):
    start_lat = _micro_lat(args, "start_lat_micro")
    start_lon = _micro_lon(args, "start_lon_micro")
    stops = args.get("stops")
    require(isinstance(stops, list) and 1 <= len(stops) <= 25, "invalid_argument", 400)
    parsed = []
    seen_ids = set()
    for stop in stops:
        require(isinstance(stop, dict), "invalid_argument", 400)
        stop_id = identifier(stop.get("stop_id"))
        require(stop_id not in seen_ids, "duplicate", 400)
        seen_ids.add(stop_id)
        parsed.append(
            (
                stop_id,
                _require_int(stop.get("lat_micro"), "invalid_argument", -90000000, 90000000),
                _require_int(stop.get("lon_micro"), "invalid_argument", -180000000, 180000000),
            )
        )
    current = (start_lat, start_lon)
    remaining = list(parsed)
    order = []
    legs = []
    total = 0
    while remaining:
        best = min(
            remaining,
            key=lambda s: (_haversine_m(current[0], current[1], s[1], s[2]), s[0]),
        )
        distance = _haversine_m(current[0], current[1], best[1], best[2])
        legs.append({"from": "start" if not order else order[-1], "to": best[0], "distance_m": distance})
        total += distance
        order.append(best[0])
        current = (best[1], best[2])
        remaining.remove(best)
    return {
        "stop_order": order,
        "legs": legs,
        "total_distance_m": total,
        "note": "greedy nearest-neighbor heuristic; not guaranteed optimal (TSP approximation)",
        "heuristic": True,
    }


def h_field_arrival_track(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    wo = _get_required(ctx, "work_order", work_order_id)
    require(wo["status"] in ("assigned", "in_progress"), "invalid_transition", 400)
    require(not wo.get("arrival"), "conflict", 409)
    observed_at, _ = _iso_arg(ctx, args, "observed_at")
    source = identifier(args.get("source"))
    max_age_s = _int_arg(args, "max_age_s", 1, _MAX_AGE_LIMIT_S, required=False, default=86400)
    age = _age_s(ctx, observed_at)
    require(age <= max_age_s, "stale_evidence", 400)
    lat = _micro_lat(args, required=False)
    lon = _micro_lon(args, required=False)
    updated = ctx.update(
        "work_order",
        work_order_id,
        {
            "status": "in_progress",
            "arrival": {"observed_at": observed_at, "source": source, "lat_micro": lat, "lon_micro": lon, "fresh": True},
        },
    )
    return {"work_order_id": work_order_id, "status": updated["status"], "arrival": updated["arrival"]}


def h_field_work_complete(ctx, args):
    updated = _complete_work_order(ctx, args)
    return {
        "work_order_id": updated["id"],
        "status": updated["status"],
        "service_activation": updated["service_activation"],
    }


def h_field_photo_upload(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    wo = _get_required(ctx, "work_order", work_order_id)
    require(wo["status"] in ("assigned", "in_progress", "completed"), "invalid_transition", 400)
    photo_ref = _id_arg(ctx, args, "photo_ref")
    taken_at, _ = _iso_arg(ctx, args, "taken_at")
    note = _text_arg(args, "note", 500, required=False)
    rec = ctx.create(
        "field_photo",
        {"work_order_id": work_order_id, "photo_ref": photo_ref, "taken_at": taken_at, "note": note},
    )
    return {"field_photo_id": rec["id"], "work_order_id": work_order_id}


def h_field_signature_capture(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    wo = _get_required(ctx, "work_order", work_order_id)
    require(wo["status"] in ("in_progress", "completed"), "invalid_transition", 400)
    signature_ref = _id_arg(ctx, args, "signature_ref")
    signed_by = _text_arg(args, "signed_by", 100)
    signed_at, _ = _iso_arg(ctx, args, "signed_at")
    rec = ctx.create(
        "field_signature",
        {"work_order_id": work_order_id, "signature_ref": signature_ref, "signed_by": signed_by, "signed_at": signed_at},
    )
    return {"field_signature_id": rec["id"], "work_order_id": work_order_id}


def h_field_equipment_use(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    wo = _get_required(ctx, "work_order", work_order_id)
    require(wo["status"] in ("assigned", "in_progress", "completed"), "invalid_transition", 400)
    sku = _id_arg(ctx, args, "sku")
    qty = _int_arg(args, "qty", 1, 10000)
    existing = ctx.list("spare_part", where={"sku": sku}, limit=_LIST_CAP)
    require(existing, "not_found", 404)
    part = existing[0]
    require(part["qty"] >= qty, "insufficient_resources", 409)
    ctx.update("spare_part", part["id"], {"qty": part["qty"] - qty})
    rec = ctx.create(
        "equipment_use",
        {"work_order_id": work_order_id, "sku": sku, "qty": qty, "spare_part_id": part["id"]},
    )
    return {"equipment_use_id": rec["id"], "sku": sku, "remaining_qty": part["qty"] - qty}


def h_field_time_track(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    _get_required(ctx, "work_order", work_order_id)
    technician_id = _id_arg(ctx, args, "technician_id")
    started_at, started = _iso_arg(ctx, args, "started_at")
    ended_at, ended = _iso_arg(ctx, args, "ended_at")
    require(ended > started, "invalid_argument", 400)
    duration_s = int((ended - started).total_seconds())
    rec = ctx.create(
        "time_entry",
        {
            "work_order_id": work_order_id,
            "technician_id": technician_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_s": duration_s,
        },
    )
    entries = ctx.list("time_entry", where={"work_order_id": work_order_id}, limit=_LIST_CAP)
    total_s = sum(e["duration_s"] for e in entries)
    return {
        "time_entry_id": rec["id"],
        "duration_s": duration_s,
        "work_order_total_s": total_s,
        "analytics": _bounded(_LIST_CAP),
    }


def h_field_reassignment(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    wo = _get_required(ctx, "work_order", work_order_id)
    require(wo["status"] in ("assigned", "in_progress"), "invalid_transition", 400)
    require(wo.get("technician_id") is not None, "validation_failed", 400)
    new_technician_id = _id_arg(ctx, args, "new_technician_id")
    require(new_technician_id != wo["technician_id"], "validation_failed", 400)
    reason = _text_arg(args, "reason", 500)
    history = list(wo.get("reassignments", []))
    history.append({"from": wo["technician_id"], "to": new_technician_id, "reason": reason, "at": ctx.now})
    updated = ctx.update(
        "work_order",
        work_order_id,
        {"technician_id": new_technician_id, "reassignments": history},
    )
    return {"work_order_id": work_order_id, "technician_id": updated["technician_id"], "reassignments": history}


def h_field_emergency_dispatch(ctx, args):
    outage_id = _id_arg(ctx, args, "outage_id", required=False)
    device_id = _id_arg(ctx, args, "device_id", required=False)
    subscriber_id = _id_arg(ctx, args, "subscriber_id", required=False)
    title = _text_arg(args, "title", 200, required=False)
    required_skills = _text_list_arg(args, "required_skills", 20)
    require(outage_id is not None or device_id is not None, "insufficient_evidence", 400)
    outage = None
    if outage_id is not None:
        outage = ctx.get("outage", outage_id)
        require(outage is not None and outage["status"] == "open", "validation_failed", 400)
        asset_id = outage["asset_id"]
    else:
        outage = _open_outage_for_asset(ctx, "device", device_id)
        require(outage is not None, "insufficient_evidence", 400)
        asset_id = device_id
    rec = ctx.create(
        "work_order",
        {
            "subscriber_id": subscriber_id,
            "provision_order_id": None,
            "technician_id": None,
            "title": title or "emergency dispatch for outage %s" % outage["id"],
            "priority": "emergency",
            "status": "open",
            "assigned_at": None,
            "arrival": None,
            "completion": None,
            "completed_at": None,
            "reassignments": [],
            "source_outage_id": outage["id"],
            "required_skills": required_skills,
            "asset_id": asset_id,
        },
    )
    return {
        "work_order_id": rec["id"],
        "status": rec["status"],
        "priority": "emergency",
        "source_outage_id": outage["id"],
    }


def h_field_skill_match(ctx, args):
    required = _text_list_arg(args, "required_skills", 20, required=True)
    require(len(required) >= 1, "invalid_argument", 400)
    required_set = {skill.strip().lower() for skill in required}
    candidates = args.get("candidates")
    require(isinstance(candidates, list) and 1 <= len(candidates) <= 100, "invalid_argument", 400)
    ranked = []
    seen = set()
    for cand in candidates:
        require(isinstance(cand, dict), "invalid_argument", 400)
        cand_id = identifier(cand.get("technician_id"))
        require(cand_id not in seen, "duplicate", 400)
        seen.add(cand_id)
        skills = cand.get("skills")
        require(isinstance(skills, list) and len(skills) <= 50, "invalid_argument", 400)
        owned = {str(skill).strip().lower() for skill in skills}
        matched = sorted(required_set & owned)
        missing = sorted(required_set - owned)
        ranked.append({"technician_id": cand_id, "score": len(matched), "matched": matched, "missing": missing})
    ranked.sort(key=lambda r: (-r["score"], r["technician_id"]))
    return {"ranking": ranked, "heuristic": True, "note": "exact skill-set matching; no proficiency weighting"}


def h_field_feedback_collect(ctx, args):
    work_order_id = _id_arg(ctx, args, "work_order_id")
    wo = _get_required(ctx, "work_order", work_order_id)
    require(wo["status"] == "completed", "invalid_transition", 400)
    existing = ctx.list("feedback", where={"work_order_id": work_order_id}, limit=_LIST_CAP)
    require(not existing, "conflict", 409)
    rating = _int_arg(args, "rating", 1, 5)
    comment = _text_arg(args, "comment", 1000, required=False)
    rec = ctx.create(
        "feedback",
        {"work_order_id": work_order_id, "rating": rating, "comment": comment},
    )
    return {"feedback_id": rec["id"], "rating": rating}


def h_sla_define(ctx, args):
    name = _text_arg(args, "name", 100)
    targets = args.get("targets")
    require(isinstance(targets, dict), "invalid_argument", 400)
    normalized = {}
    for sev in _SEVERITIES:
        entry = targets.get(sev)
        require(isinstance(entry, dict), "invalid_argument", 400)
        response_minutes = _require_int(entry.get("response_minutes"), "invalid_argument", 1, 10080)
        resolution_minutes = _require_int(entry.get("resolution_minutes"), "invalid_argument", 1, 10080)
        require(resolution_minutes >= response_minutes, "invalid_argument", 400)
        normalized[sev] = {"response_minutes": response_minutes, "resolution_minutes": resolution_minutes}
    penalty_bps_per_hour = _int_arg(args, "penalty_bps_per_hour", 0, 1000, required=False)
    penalty_cap_pct = _int_arg(args, "penalty_cap_pct", 0, 100, required=False)
    rec = ctx.create(
        "sla",
        {
            "name": name,
            "targets": normalized,
            "penalty_bps_per_hour": penalty_bps_per_hour,
            "penalty_cap_pct": penalty_cap_pct,
        },
    )
    return {"sla_id": rec["id"], "name": name}


def _sla_evaluation(ctx, sla, ticket, as_of=None):
    cutoff=parse_time(as_of or ctx.now)
    targets = sla["targets"][ticket.get("severity", "P3")]
    opened = parse_time(ticket["opened_at"])
    response = {"target_minutes": targets["response_minutes"]}
    if ticket.get("assigned_at") and parse_time(ticket["assigned_at"]) <= cutoff:
        actual = int((parse_time(ticket["assigned_at"]) - opened).total_seconds() // 60)
        response.update({"actual_minutes": actual, "breached": actual > targets["response_minutes"], "pending": False})
    else:
        pending_minutes = max(0,int((cutoff - opened).total_seconds() // 60))
        response.update(
            {
                "actual_minutes": None,
                "breached": pending_minutes > targets["response_minutes"],
                "pending": True,
                "pending_minutes": pending_minutes,
            }
        )
    resolution = {"target_minutes": targets["resolution_minutes"]}
    if ticket.get("resolved_at") and parse_time(ticket["resolved_at"]) <= cutoff:
        actual = int((parse_time(ticket["resolved_at"]) - opened).total_seconds() // 60)
        resolution.update({"actual_minutes": actual, "breached": actual > targets["resolution_minutes"], "pending": False})
    else:
        pending_minutes = max(0,int((cutoff - opened).total_seconds() // 60))
        resolution.update(
            {
                "actual_minutes": None,
                "breached": pending_minutes > targets["resolution_minutes"],
                "pending": True,
                "pending_minutes": pending_minutes,
            }
        )
    return {
        "ticket_id": ticket["id"],
        "severity": ticket.get("severity", "P3"),
        "status": ticket["status"],
        "response": response,
        "resolution": resolution,
        "breached": response["breached"] or resolution["breached"],
    }


def h_sla_monitor(ctx, args):
    sla_id = _id_arg(ctx, args, "sla_id")
    sla = _get_required(ctx, "sla", sla_id)
    ticket_ids = _id_list_arg(args, "ticket_ids", 100)
    evaluations = [_sla_evaluation(ctx, sla, _ticket_get(ctx, tid)) for tid in ticket_ids]
    breached = sum(1 for e in evaluations if e["breached"])
    return {
        "sla_id": sla_id,
        "evaluations": evaluations,
        "tickets_evaluated": len(evaluations),
        "breached_count": breached,
        "analytics": _bounded(100),
    }


def h_sla_breach_alert(ctx, args):
    sla_id = _id_arg(ctx, args, "sla_id")
    sla = _get_required(ctx, "sla", sla_id)
    ticket_ids = _id_list_arg(args, "ticket_ids", 100)
    created = []
    already = []
    evaluations = [_sla_evaluation(ctx, sla, _ticket_get(ctx, tid)) for tid in ticket_ids]
    existing_alerts = ctx.list("alert", where={"type": "sla_breach"}, limit=_LIST_CAP)
    existing_keys = {(a.get("ticket_id"), a.get("sla_id")) for a in existing_alerts}
    for evaluation in evaluations:
        key = (evaluation["ticket_id"], sla_id)
        if not evaluation["breached"]:
            continue
        if key in existing_keys:
            already.append(evaluation["ticket_id"])
            continue
        rec = ctx.create(
            "alert",
            {
                "type": "sla_breach",
                "sla_id": sla_id,
                "ticket_id": evaluation["ticket_id"],
                "severity": evaluation["severity"],
                "breached_response": evaluation["response"]["breached"],
                "breached_resolution": evaluation["resolution"]["breached"],
            },
        )
        existing_keys.add(key)
        created.append(rec["id"])
    ctx.event("sla.breach.alert", {"sla_id": sla_id, "alerts_created": created})
    return {
        "sla_id": sla_id,
        "alert_ids": created,
        "already_alerted_ticket_ids": already,
        "evaluation_count": len(evaluations),
    }


def h_sla_report_customer(ctx, args):
    subscriber_id = _id_arg(ctx, args, "subscriber_id")
    _get_required(ctx,"subscriber",subscriber_id)
    period_start, start = _iso_arg(ctx, args, "period_start")
    period_end, end = _iso_arg(ctx, args, "period_end")
    require(start < end, "invalid_argument", 400)
    require(int((end - start).total_seconds()) <= 92 * 86400, "invalid_argument", 400)
    sla_id = _id_arg(ctx, args, "sla_id", required=False)
    tickets = [
        t
        for t in ctx.list("ticket", where={"subscriber_id": subscriber_id}, limit=_LIST_CAP)
        if parse_time(t["opened_at"]) < end and (not t.get('resolved_at') or parse_time(t['resolved_at']) >= start)
    ]
    evaluations = None
    if sla_id is not None:
        sla = _get_required(ctx, "sla", sla_id)
        evaluations = [_sla_evaluation(ctx, sla, t,as_of=period_end) for t in tickets]
    outages = ctx.list("outage", limit=_LIST_CAP)
    resources=ctx.list('resource',where={'subscriber_id':subscriber_id},limit=_LIST_CAP)
    devices={r['id']:r for r in ctx.list('device',limit=_LIST_CAP)}
    asset_ids={r['id'] for r in resources};asset_ids.update(r['device_id'] for r in resources if r.get('device_id'))
    asset_ids.update(t['device_id'] for t in tickets if t.get('device_id'))
    for asset in list(asset_ids):
        seen=set();node=devices.get(asset)
        while node and node.get('parent_id'):
            parent=node['parent_id']
            require(parent not in seen,'topology_cycle',409);seen.add(parent);asset_ids.add(parent);node=devices.get(parent)
    intervals = []
    ignored=0
    for outage in outages:
        correlated=(outage.get('correlation') or {}).get('distinct_subscribers',[])
        if outage.get('subscriber_id')!=subscriber_id and outage.get('asset_id') not in asset_ids and not any(x.get('subscriber_id')==subscriber_id for x in correlated):
            ignored+=1;continue
        outage_start = parse_time(outage["started_at"])
        outage_end = parse_time(outage["ended_at"]) if outage.get("ended_at") else parse_time(ctx.now)
        lo = max(start, outage_start)
        hi = min(end, outage_end)
        if hi > lo:
            intervals.append((lo, hi))
    merged = _merge_intervals([(int(s.timestamp()), int(e.timestamp())) for s, e in intervals])
    downtime_s = sum(e - s for s, e in merged)
    window_s = int((end - start).total_seconds())
    availability_bps = (window_s - downtime_s) * 10000 // window_s if window_s else 10000
    return {
        "subscriber_id": subscriber_id,
        "period_start": period_start,
        "period_end": period_end,
        "tickets_count": len(tickets),
        "sla_evaluations": evaluations,
        "outage_intervals_merged": len(merged),
        "downtime_s_union": downtime_s,
        "availability_bps": availability_bps,
        "availability_basis": "attributed_recorded_outages_only",
        "live_measurement_complete": False,
        "unattributed_outages_ignored": ignored,
        "calculation_only": True,
        "note": "downtime uses union of overlapping outage windows; bounded analytics",
        "analytics": _bounded(_LIST_CAP),
    }


def h_sla_penalty_calc(ctx, args):
    sla_id = _id_arg(ctx, args, "sla_id")
    sla = _get_required(ctx, "sla", sla_id)
    ticket_id = _id_arg(ctx, args, "ticket_id")
    ticket = _ticket_get(ctx, ticket_id)
    monthly_charge_idr = money(args.get("monthly_charge_idr"))
    require(sla.get("penalty_bps_per_hour") is not None, "validation_failed", 400)
    require(ticket.get("resolved_at") is not None, "validation_failed", 400)
    evaluation = _sla_evaluation(ctx, sla, ticket)
    require(evaluation["resolution"]["breached"], "validation_failed", 400)
    breach_minutes = evaluation["resolution"]["actual_minutes"] - evaluation["resolution"]["target_minutes"]
    penalty_bps_per_hour = sla["penalty_bps_per_hour"]
    penalty_idr = (monthly_charge_idr * penalty_bps_per_hour * breach_minutes) // (10000 * 60)
    capped = False
    if sla.get("penalty_cap_pct") is not None:
        cap_idr = monthly_charge_idr * sla["penalty_cap_pct"] // 100
        if penalty_idr > cap_idr:
            penalty_idr = cap_idr
            capped = True
    return {
        "sla_id": sla_id,
        "ticket_id": ticket_id,
        "breach_minutes": breach_minutes,
        "penalty_bps_per_hour": penalty_bps_per_hour,
        "monthly_charge_idr": monthly_charge_idr,
        "penalty_idr": penalty_idr,
        "capped": capped,
        "formula": "penalty_idr = charge * bps_per_hour * breach_minutes // (10000*60); integer math only",
        "calculation_only": True,
    }


def h_qos_measure(ctx, args):
    subscriber_id = _id_arg(ctx, args, "subscriber_id", required=False)
    device_id = _id_arg(ctx, args, "device_id", required=False)
    require(subscriber_id is not None or device_id is not None, "invalid_argument", 400)
    max_age_s = _int_arg(args, "max_age_s", 1, _MAX_AGE_LIMIT_S, required=False, default=3600)
    samples = args.get("samples")
    require(isinstance(samples, list) and 1 <= len(samples) <= 200, "insufficient_evidence", 400)
    normalized = []
    for sample in samples:
        require(isinstance(sample, dict), "invalid_argument", 400)
        normalized.append(
            {
                "source": sample.get("source"),
                "observed_at": sample.get("observed_at"),
                "value_int": None,
                "latency_ms": _require_int(sample.get("latency_ms"), "invalid_argument", 0, 60000),
                "jitter_ms": _require_int(sample.get("jitter_ms"), "invalid_argument", 0, 5000),
                "loss_bps": _require_int(sample.get("loss_bps"), "invalid_argument", 0, 10000),
            }
        )
    freshness = _fresh_samples(ctx, normalized, max_age_s)
    fresh = freshness["fresh"]
    if fresh:
        avg_latency = sum(s["latency_ms"] for s in fresh) // len(fresh)
        avg_jitter = sum(s["jitter_ms"] for s in fresh) // len(fresh)
        avg_loss = sum(s["loss_bps"] for s in fresh) // len(fresh)
    else:
        avg_latency = avg_jitter = avg_loss = None
    thresholds = args.get("thresholds") or {}
    warn_latency = _require_int(thresholds.get("latency_ms", 100), "invalid_argument", 1, 60000)
    warn_jitter = _require_int(thresholds.get("jitter_ms", 30), "invalid_argument", 1, 5000)
    warn_loss = _require_int(thresholds.get("loss_bps", 100), "invalid_argument", 1, 10000)
    breaches = []
    if avg_latency is not None:
        if avg_latency > warn_latency:
            breaches.append("latency_ms")
        if avg_jitter > warn_jitter:
            breaches.append("jitter_ms")
        if avg_loss > warn_loss:
            breaches.append("loss_bps")
    return {
        "subscriber_id": subscriber_id,
        "device_id": device_id,
        "fresh_count": len(fresh),
        "stale_count": freshness["stale_count"],
        "duplicates_ignored": freshness["duplicates_ignored"],
        "future_ignored": freshness["future_ignored"],
        "avg_latency_ms": avg_latency,
        "avg_jitter_ms": avg_jitter,
        "avg_loss_bps": avg_loss,
        "thresholds": {"latency_ms": warn_latency, "jitter_ms": warn_jitter, "loss_bps": warn_loss},
        "breached_metrics": breaches,
        "status": "degraded" if breaches else ("stale" if not fresh else "ok"),
        "evidence_based": True,
        "note": "default thresholds are heuristic; override via thresholds argument",
    }


def h_trouble_ticket_create(ctx, args):
    rec = _record_ticket(ctx, "trouble", args)
    return {"ticket_id": rec["id"], "status": rec["status"], "severity": rec["severity"]}


def h_trouble_ticket_escalate(ctx, args):
    ticket_id = _id_arg(ctx, args, "ticket_id")
    ticket = _ticket_get(ctx, ticket_id)
    require(ticket["status"] in ("open", "assigned"), "invalid_transition", 400)
    reason = _text_arg(args, "reason", 500)
    severity = ticket.get("severity", "P3")
    require(severity != "P1", "severity_max", 409)
    new_severity = "P1" if severity == "P2" else "P2"
    history = list(ticket.get("history", []))
    history.append({"action": "escalated", "from": severity, "to": new_severity, "reason": reason, "at": ctx.now})
    updated = ctx.update("ticket", ticket_id, {"severity": new_severity, "history": history})
    return {"ticket_id": ticket_id, "severity": updated["severity"], "history": history}


def h_trouble_ticket_resolve(ctx, args):
    ticket_id = _id_arg(ctx, args, "ticket_id")
    ticket = _ticket_get(ctx, ticket_id)
    require(ticket["status"] in ("open", "assigned"), "invalid_transition", 400)
    note = _text_arg(args, "note", 2000)
    evidence_refs = _id_list_arg(args, "evidence_refs", 10, required=False, default=[])
    verified_at, _ = _iso_arg(ctx, args, "verified_at", required=False)
    updated = ctx.update(
        "ticket",
        ticket_id,
        {
            "status": "resolved",
            "resolved_at": ctx.now,
            "resolution": {
                "note": note,
                "evidence_refs": evidence_refs,
                "verified_at": verified_at,
                "source": "operator_supplied",
            },
        },
    )
    return {
        "ticket_id": ticket_id,
        "status": updated["status"],
        "external_verification": "not_performed",
        "resolution": updated["resolution"],
    }


_S = {
    "id": {"type": "string", "minLength": 1, "maxLength": 100},
    "iso": {"type": "string", "minLength": 10, "maxLength": 40},
    "sev": {"enum": ["P1", "P2", "P3"]},
    "text200": {"type": "string", "minLength": 1, "maxLength": 200},
    "text2000": {"type": "string", "minLength": 1, "maxLength": 2000},
}


def _sch(props, required=None):
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


_LAT = {"type": "integer", "minimum": -90000000, "maximum": 90000000}
_LON = {"type": "integer", "minimum": -180000000, "maximum": 180000000}

_SAMPLE = {
    "type": "object",
    "properties": {
        "value_int": {"type": "integer"},
        "observed_at": _S["iso"],
        "source": _S["id"],
    },
    "required": ["value_int", "observed_at", "source"],
    "additionalProperties": False,
}

_QOS_SAMPLE = {
    "type": "object",
    "properties": {
        "latency_ms": {"type": "integer", "minimum": 0, "maximum": 60000},
        "jitter_ms": {"type": "integer", "minimum": 0, "maximum": 5000},
        "loss_bps": {"type": "integer", "minimum": 0, "maximum": 10000},
        "observed_at": _S["iso"],
        "source": _S["id"],
    },
    "required": ["latency_ms", "jitter_ms", "loss_bps", "observed_at", "source"],
    "additionalProperties": False,
}

_OBSERVATION = {
    "type": "object",
    "properties": {
        "subscriber_id": _S["id"],
        "observed_at": _S["iso"],
        "source": _S["id"],
        "detail": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "required": ["subscriber_id", "observed_at", "source"],
    "additionalProperties": False,
}

_TECH_CANDIDATE = {
    "type": "object",
    "properties": {"technician_id": _S["id"], "lat_micro": _LAT, "lon_micro": _LON},
    "required": ["technician_id", "lat_micro", "lon_micro"],
    "additionalProperties": False,
}

_SKILL_CANDIDATE = {
    "type": "object",
    "properties": {"technician_id": _S["id"], "skills": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 50}, "maxItems": 50}},
    "required": ["technician_id", "skills"],
    "additionalProperties": False,
}

_ROUTE_STOP = {
    "type": "object",
    "properties": {"stop_id": _S["id"], "lat_micro": _LAT, "lon_micro": _LON},
    "required": ["stop_id", "lat_micro", "lon_micro"],
    "additionalProperties": False,
}

_PORT_SPEC = {
    "type": "object",
    "properties": {"port_code": {"type": "string", "minLength": 1, "maxLength": 50}},
    "required": ["port_code"],
    "additionalProperties": False,
}

_REQUIRED_RESOURCE = {
    "type": "object",
    "properties": {
        "resource_type": {"enum": sorted(_RESOURCE_TYPES)},
        "count": {"type": "integer", "minimum": 1, "maximum": 1000},
    },
    "required": ["resource_type", "count"],
    "additionalProperties": False,
}

_SLA_TARGET = {
    "type": "object",
    "properties": {
        "response_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
        "resolution_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
    },
    "required": ["response_minutes", "resolution_minutes"],
    "additionalProperties": False,
}

_SAMPLES_ARRAY = {"type": "array", "items": _SAMPLE, "minItems": 1, "maxItems": 1000}

HANDLERS = {}
SCHEMAS = {}
MUTATIONS = frozenset()


def _build():
    global MUTATIONS
    mutations = set()
    table = [
        (
            "netinv.device.register",
            h_netinv_device_register,
            _sch(
                {
                    "name": {"type": "string", "minLength": 1, "maxLength": 100},
                    "device_type": {"enum": sorted(_DEVICE_TYPES)},
                    "status": {"enum": sorted(_DEVICE_STATUS - {"retired"})},
                    "site": {"type": "string", "minLength": 1, "maxLength": 100},
                    "lat_micro": _LAT,
                    "lon_micro": _LON,
                    "parent_id": _S["id"],
                    "capacity_ports": {"type": "integer", "minimum": 0, "maximum": 4096},
                },
                ["name", "device_type"],
            ),
            True,
        ),
        (
            "netinv.device.update",
            h_netinv_device_update,
            _sch(
                {
                    "device_id": _S["id"],
                    "site": {"type": "string", "minLength": 1, "maxLength": 100},
                    "lat_micro": _LAT,
                    "lon_micro": _LON,
                    "parent_id": _S["id"],
                    "capacity_ports": {"type": "integer", "minimum": 0, "maximum": 4096},
                    "status": {"enum": sorted(_DEVICE_STATUS)},
                },
                ["device_id"],
            ),
            True,
        ),
        (
            "netinv.device.decommission",
            h_netinv_device_decommission,
            _sch({"device_id": _S["id"], "reason": {"type": "string", "minLength": 1, "maxLength": 500}}, ["device_id"]),
            True,
        ),
        (
            "netinv.hierarchy.build",
            h_netinv_hierarchy_build,
            _sch({"root_device_id": _S["id"]}),
            False,
        ),
        (
            "netinv.port.management",
            h_netinv_port_management,
            _sch({"device_id": _S["id"], "ports": {"type": "array", "items": _PORT_SPEC, "minItems": 1, "maxItems": 500}}, ["device_id"]),
            True,
        ),
        (
            "netinv.link.management",
            h_netinv_link_management,
            _sch(
                {
                    "a_device_id": _S["id"],
                    "b_device_id": _S["id"],
                    "link_type": {"enum": sorted(_LINK_TYPES)},
                    "length_m": {"type": "integer", "minimum": 0, "maximum": 500000},
                    "status": {"enum": ["active", "planned"]},
                }
            ),
            True,
        ),
        (
            "netinv.cable.trace",
            h_netinv_cable_trace,
            _sch({"from_device_id": _S["id"], "to_device_id": _S["id"]}, ["from_device_id", "to_device_id"]),
            False,
        ),
        (
            "netinv.spatial.view",
            h_netinv_spatial_view,
            _sch(
                {
                    "center_lat_micro": _LAT,
                    "center_lon_micro": _LON,
                    "radius_m": {"type": "integer", "minimum": 1, "maximum": 100000},
                },
                ["center_lat_micro", "center_lon_micro", "radius_m"],
            ),
            False,
        ),
        (
            "netinv.capacity.track",
            h_netinv_capacity_track,
            _sch({"device_id": _S["id"]}),
            False,
        ),
        (
            "netinv.utilization.track",
            h_netinv_utilization_track,
            _sch(
                {
                    "device_id": _S["id"],
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                    "interfaces": {
                        "type": "array",
                        "items": _SAMPLE,
                        "minItems": 1,
                        "maxItems": 200,
                    },
                },
                ["device_id", "interfaces"],
            ),
            False,
        ),
        (
            "netinv.olt.port.alloc",
            h_netinv_olt_port_alloc,
            _sch(
                {"resource_id": _S["id"], "subscriber_id": _S["id"], "provision_order_id": _S["id"]},
                ["resource_id", "subscriber_id"],
            ),
            True,
        ),
        (
            "netinv.spare.parts",
            h_netinv_spare_parts,
            _sch(
                {
                    "sku": _S["id"],
                    "name": {"type": "string", "minLength": 1, "maxLength": 100},
                    "qty": {"type": "integer", "minimum": 0, "maximum": 1000000},
                    "adjust_delta": {"type": "integer", "minimum": -1000000, "maximum": 1000000},
                }
            ),
            True,
        ),
        (
            "prov.order.create",
            h_prov_order_create,
            _sch(
                {
                    "subscriber_id": _S["id"],
                    "product_id": _S["id"],
                    "required_resources": {"type": "array", "items": _REQUIRED_RESOURCE, "minItems": 1, "maxItems": 10},
                },
                ["subscriber_id"],
            ),
            True,
        ),
        (
            "prov.order.validate",
            h_prov_order_validate,
            _sch({"provision_order_id": _S["id"]}, ["provision_order_id"]),
            True,
        ),
        (
            "prov.allocate.resource",
            h_prov_allocate_resource,
            _sch(
                {
                    "provision_order_id": _S["id"],
                    "resource_type": {"enum": sorted(_RESOURCE_TYPES)},
                    "count": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                ["provision_order_id", "resource_type", "count"],
            ),
            True,
        ),
        (
            "prov.workorder.create",
            h_prov_workorder_create,
            _sch(
                {
                    "subscriber_id": _S["id"],
                    "provision_order_id": _S["id"],
                    "technician_id": _S["id"],
                    "title": _S["text200"],
                    "priority": {"enum": ["emergency", "high", "normal"]},
                },
                ["subscriber_id"],
            ),
            True,
        ),
        (
            "prov.workorder.complete",
            h_prov_workorder_complete,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "note": _S["text2000"],
                    "evidence_refs": {"type": "array", "items": _S["id"], "maxItems": 10},
                    "completed_at": _S["iso"],
                    "signature_ref": _S["id"],
                },
                ["work_order_id", "note"],
            ),
            True,
        ),
        (
            "noc.monitor.uptime",
            h_noc_monitor_uptime,
            _sch(
                {
                    "device_id": _S["id"],
                    "samples": _SAMPLES_ARRAY,
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                    "target_uptime_bps": {"type": "integer", "minimum": 1, "maximum": 10000},
                },
                ["device_id", "samples"],
            ),
            False,
        ),
        (
            "noc.monitor.bandwidth",
            h_noc_monitor_bandwidth,
            _sch(
                {
                    "device_id": _S["id"],
                    "samples": _SAMPLES_ARRAY,
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                    "warn_threshold": {"type": "integer", "minimum": 0, "maximum": 100000000},
                },
                ["device_id", "samples"],
            ),
            False,
        ),
        (
            "noc.monitor.optical",
            h_noc_monitor_optical,
            _sch(
                {
                    "device_id": _S["id"],
                    "samples": _SAMPLES_ARRAY,
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                    "warn_threshold": {"type": "integer", "minimum": -8000, "maximum": 0},
                },
                ["device_id", "samples"],
            ),
            False,
        ),
        (
            "noc.monitor.temperature",
            h_noc_monitor_temperature,
            _sch(
                {
                    "device_id": _S["id"],
                    "samples": _SAMPLES_ARRAY,
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                    "warn_threshold": {"type": "integer", "minimum": -40, "maximum": 100},
                },
                ["device_id", "samples"],
            ),
            False,
        ),
        (
            "noc.alert.threshold",
            h_noc_alert_threshold,
            _sch(
                {
                    "device_id": _S["id"],
                    "metric_name": {"type": "string", "minLength": 1, "maxLength": 50},
                    "samples": _SAMPLES_ARRAY,
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                    "warn_threshold": {"type": "integer", "minimum": -(10**12), "maximum": 10**12},
                },
                ["device_id", "samples", "warn_threshold"],
            ),
            True,
        ),
        (
            "noc.alert.fiber.cut",
            h_noc_alert_fiber_cut,
            _sch(
                {
                    "device_id": _S["id"],
                    "observed_at": _S["iso"],
                    "source": _S["id"],
                    "detail": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                },
                ["device_id", "observed_at", "source"],
            ),
            True,
        ),
        (
            "noc.alert.outage",
            h_noc_alert_outage,
            _sch(
                {
                    "asset_id": _S["id"],
                    "asset_kind": {"enum": ["device", "resource"]},
                    "observations": {"type": "array", "items": _OBSERVATION, "minItems": 1, "maxItems": 1000},
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                    "min_subscribers": {"type": "integer", "minimum": 2, "maximum": 1000},
                    "window_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                },
                ["asset_id", "observations"],
            ),
            True,
        ),
        (
            "noc.ticket.create",
            h_noc_ticket_create,
            _sch(
                {
                    "title": _S["text200"],
                    "detail": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "severity": _S["sev"],
                    "device_id": _S["id"],
                    "subscriber_id": _S["id"],
                },
                ["title"],
            ),
            True,
        ),
        (
            "noc.ticket.assign",
            h_noc_ticket_assign,
            _sch({"ticket_id": _S["id"], "assignee": _S["id"]}, ["ticket_id", "assignee"]),
            True,
        ),
        (
            "noc.escalation.matrix",
            h_noc_escalation_matrix,
            _sch({}),
            False,
        ),
        (
            "noc.dashboard.realtime",
            h_noc_dashboard_realtime,
            _sch({}),
            False,
        ),
        (
            "field.dispatch.create",
            h_field_dispatch_create,
            _sch(
                {
                    "subscriber_id": _S["id"],
                    "provision_order_id": _S["id"],
                    "title": _S["text200"],
                    "priority": {"enum": ["emergency", "high", "normal"]},
                    "window_start": _S["iso"],
                    "window_end": _S["iso"],
                    "site_lat_micro": _LAT,
                    "site_lon_micro": _LON,
                    "required_skills": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 50},
                        "maxItems": 20,
                    },
                },
                ["subscriber_id"],
            ),
            True,
        ),
        (
            "field.dispatch.assign",
            h_field_dispatch_assign,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "technician_id": _S["id"],
                    "candidates": {"type": "array", "items": _TECH_CANDIDATE, "minItems": 1, "maxItems": 200},
                },
                ["work_order_id"],
            ),
            True,
        ),
        (
            "field.route.optimize",
            h_field_route_optimize,
            _sch(
                {
                    "start_lat_micro": _LAT,
                    "start_lon_micro": _LON,
                    "stops": {"type": "array", "items": _ROUTE_STOP, "minItems": 1, "maxItems": 25},
                },
                ["start_lat_micro", "start_lon_micro", "stops"],
            ),
            False,
        ),
        (
            "field.arrival.track",
            h_field_arrival_track,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "observed_at": _S["iso"],
                    "source": _S["id"],
                    "lat_micro": _LAT,
                    "lon_micro": _LON,
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                },
                ["work_order_id", "observed_at", "source"],
            ),
            True,
        ),
        (
            "field.work.complete",
            h_field_work_complete,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "note": _S["text2000"],
                    "evidence_refs": {"type": "array", "items": _S["id"], "maxItems": 10},
                    "completed_at": _S["iso"],
                    "signature_ref": _S["id"],
                },
                ["work_order_id", "note"],
            ),
            True,
        ),
        (
            "field.photo.upload",
            h_field_photo_upload,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "photo_ref": _S["id"],
                    "taken_at": _S["iso"],
                    "note": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                ["work_order_id", "photo_ref", "taken_at"],
            ),
            True,
        ),
        (
            "field.signature.capture",
            h_field_signature_capture,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "signature_ref": _S["id"],
                    "signed_by": {"type": "string", "minLength": 1, "maxLength": 100},
                    "signed_at": _S["iso"],
                },
                ["work_order_id", "signature_ref", "signed_by", "signed_at"],
            ),
            True,
        ),
        (
            "field.equipment.use",
            h_field_equipment_use,
            _sch(
                {"work_order_id": _S["id"], "sku": _S["id"], "qty": {"type": "integer", "minimum": 1, "maximum": 10000}},
                ["work_order_id", "sku", "qty"],
            ),
            True,
        ),
        (
            "field.time.track",
            h_field_time_track,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "technician_id": _S["id"],
                    "started_at": _S["iso"],
                    "ended_at": _S["iso"],
                },
                ["work_order_id", "technician_id", "started_at", "ended_at"],
            ),
            True,
        ),
        (
            "field.reassignment",
            h_field_reassignment,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "new_technician_id": _S["id"],
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                ["work_order_id", "new_technician_id", "reason"],
            ),
            True,
        ),
        (
            "field.emergency.dispatch",
            h_field_emergency_dispatch,
            _sch(
                {
                    "outage_id": _S["id"],
                    "device_id": _S["id"],
                    "subscriber_id": _S["id"],
                    "title": _S["text200"],
                    "required_skills": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 50},
                        "maxItems": 20,
                    },
                }
            ),
            True,
        ),
        (
            "field.skill.match",
            h_field_skill_match,
            _sch(
                {
                    "required_skills": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 50},
                        "minItems": 1,
                        "maxItems": 20,
                    },
                    "candidates": {"type": "array", "items": _SKILL_CANDIDATE, "minItems": 1, "maxItems": 100},
                },
                ["required_skills", "candidates"],
            ),
            False,
        ),
        (
            "field.feedback.collect",
            h_field_feedback_collect,
            _sch(
                {
                    "work_order_id": _S["id"],
                    "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                    "comment": {"type": "string", "minLength": 1, "maxLength": 1000},
                },
                ["work_order_id", "rating"],
            ),
            True,
        ),
        (
            "sla.define",
            h_sla_define,
            _sch(
                {
                    "name": {"type": "string", "minLength": 1, "maxLength": 100},
                    "targets": {
                        "type": "object",
                        "properties": {"P1": _SLA_TARGET, "P2": _SLA_TARGET, "P3": _SLA_TARGET},
                        "required": ["P1", "P2", "P3"],
                        "additionalProperties": False,
                    },
                    "penalty_bps_per_hour": {"type": "integer", "minimum": 0, "maximum": 1000},
                    "penalty_cap_pct": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                ["name", "targets"],
            ),
            True,
        ),
        (
            "sla.monitor",
            h_sla_monitor,
            _sch({"sla_id": _S["id"], "ticket_ids": {"type": "array", "items": _S["id"], "minItems": 1, "maxItems": 100}}, ["sla_id", "ticket_ids"]),
            False,
        ),
        (
            "sla.breach.alert",
            h_sla_breach_alert,
            _sch({"sla_id": _S["id"], "ticket_ids": {"type": "array", "items": _S["id"], "minItems": 1, "maxItems": 100}}, ["sla_id", "ticket_ids"]),
            True,
        ),
        (
            "sla.report.customer",
            h_sla_report_customer,
            _sch(
                {
                    "subscriber_id": _S["id"],
                    "period_start": _S["iso"],
                    "period_end": _S["iso"],
                    "sla_id": _S["id"],
                },
                ["subscriber_id", "period_start", "period_end"],
            ),
            False,
        ),
        (
            "sla.penalty.calc",
            h_sla_penalty_calc,
            _sch(
                {
                    "sla_id": _S["id"],
                    "ticket_id": _S["id"],
                    "monthly_charge_idr": {"type": "integer", "minimum": 0, "maximum": 10**15},
                },
                ["sla_id", "ticket_id", "monthly_charge_idr"],
            ),
            False,
        ),
        (
            "qos.measure",
            h_qos_measure,
            _sch(
                {
                    "subscriber_id": _S["id"],
                    "device_id": _S["id"],
                    "samples": {"type": "array", "items": _QOS_SAMPLE, "minItems": 1, "maxItems": 200},
                    "max_age_s": {"type": "integer", "minimum": 1, "maximum": _MAX_AGE_LIMIT_S},
                    "thresholds": {
                        "type": "object",
                        "properties": {
                            "latency_ms": {"type": "integer", "minimum": 1, "maximum": 60000},
                            "jitter_ms": {"type": "integer", "minimum": 1, "maximum": 5000},
                            "loss_bps": {"type": "integer", "minimum": 1, "maximum": 10000},
                        },
                        "additionalProperties": False,
                    },
                },
                ["samples"],
            ),
            False,
        ),
        (
            "trouble.ticket.create",
            h_trouble_ticket_create,
            _sch(
                {
                    "title": _S["text200"],
                    "detail": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "severity": _S["sev"],
                    "device_id": _S["id"],
                    "subscriber_id": _S["id"],
                },
                ["title"],
            ),
            True,
        ),
        (
            "trouble.ticket.escalate",
            h_trouble_ticket_escalate,
            _sch({"ticket_id": _S["id"], "reason": {"type": "string", "minLength": 1, "maxLength": 500}}, ["ticket_id", "reason"]),
            True,
        ),
        (
            "trouble.ticket.resolve",
            h_trouble_ticket_resolve,
            _sch(
                {
                    "ticket_id": _S["id"],
                    "note": _S["text2000"],
                    "evidence_refs": {"type": "array", "items": _S["id"], "maxItems": 10},
                    "verified_at": _S["iso"],
                },
                ["ticket_id", "note"],
            ),
            True,
        ),
    ]
    for handler_id, handler, schema, is_mutation in table:
        assert handler_id not in HANDLERS
        HANDLERS[handler_id] = handler
        SCHEMAS[handler_id] = schema
        if is_mutation:
            mutations.add(handler_id)
    MUTATIONS = frozenset(mutations)


_build()

_LIMITATION_UNAVAILABLE = {
    "netinv.fiber.route": "not implemented: fiber route registry requires route/cable segment model; inventory links (netinv.link.management) cover adjacency only",
    "netinv.splitter.management": "not implemented: splitter port accounting needs dedicated splitter records; model as device + olt_port resources for now",
    "netinv.audit.physical": "not implemented: requires physical audit evidence adapter (scanned port inventory); no fabricated audits",
    "prov.configure.olt": "unavailable: requires verified OLT device adapter (NETCONF/SSH); local module never fabricates device configuration",
    "prov.configure.ont": "unavailable: requires verified ONT device adapter; local module never fabricates device configuration",
    "prov.activate.service": "unavailable: live network activation requires verified external adapter; local provisioning stops at resources_reserved/awaiting_connector",
    "prov.test.connectivity": "unavailable: end-to-end connectivity test requires live device adapter; no fabricated ping/speedtest results",
    "prov.suspend.service": "unavailable: suspending live service requires verified external adapter; local records only",
    "prov.unsuspend.service": "unavailable: requires verified external adapter",
    "prov.terminate.service": "unavailable: terminating live service requires verified external adapter; local records only",
    "prov.change.plan": "not implemented: plan change pricing/proration is commercial-worker scope; connector activation unavailable",
    "prov.migrate.customer": "not implemented: migration execution needs OLT adapter; local planning records only",
    "prov.relocate.service": "not implemented: relocation execution needs field + adapter coordination",
    "noc.monitor.cpu": "not implemented: same evidence-evaluator pattern as noc.monitor.temperature/optical exists; wire on request",
    "noc.map.network": "not implemented: live topology map requires live device state; netinv.hierarchy.build + links cover static inventory graph",
    "noc.shift.handover": "not implemented: shift handover document record not built yet",
    "noc.incident.postmortem": "not implemented: post-mortem document record not built yet; RCA content would be operator-supplied only",
    "field.schedule.optimize": "not implemented: greedy route optimization exists (field.route.optimize); day-level scheduling needs shift/availability model",
    "field.quality.score": "not implemented: requires accumulated feedback history analytics; field.feedback.collect stores inputs",
    "qos.threshold": "not implemented: per-call thresholds supported in qos.measure; persistent threshold profiles not built",
    "qos.speedtest.auto": "unavailable: automatic scheduled speedtests require a measurement agent/adapter; no fabricated speedtest evidence",
    "qos.benchmark": "unavailable: competitor benchmark data requires verified external data source",
}

LIMITATIONS = dict(_LIMITATION_UNAVAILABLE)
LIMITATIONS.update(
    {
        "netinv.spatial.view": "bounded analytics over inventory coordinates; proximity listing only, does not promise service coverage",
        "netinv.capacity.track": "bounded analytics over resource records (max 1000); port-based capacity only, no live throughput",
        "netinv.utilization.track": "evidence-based only; utilization comes from operator-supplied interface samples with freshness window",
        "noc.monitor.uptime": "evidence-based only: computes from operator-supplied samples vs ctx.now; no ICMP/SNMP polling",
        "noc.monitor.bandwidth": "evidence-based only; no live interface counters",
        "noc.monitor.optical": "evidence-based only; optical power in integer hundredths of dBm; warn default -27.00 dBm is heuristic",
        "noc.monitor.temperature": "evidence-based only; warn default 60C is heuristic",
        "noc.alert.threshold": "creates local alert records from supplied evidence; no paging/notification delivery",
        "noc.alert.outage": "correlation rule: distinct subscribers reporting same asset within window; single-report or repeated-report cases are not declared mass outage",
        "noc.escalation.matrix": "default reference matrix (P1 15/240m, P2 60/1440m, P3 240/4320m); override via sla.define",
        "noc.dashboard.realtime": "snapshot of stored tenant records as-of ctx.now; not live; bounded analytics (max 1000 per kind)",
        "field.dispatch.assign": "nearest-technician heuristic uses straight-line haversine distance; no traffic/schedule optimization",
        "field.route.optimize": "greedy nearest-neighbor approximation; not guaranteed optimal TSP",
        "field.skill.match": "exact normalized skill-set matching; no proficiency levels",
        "sla.report.customer": "bounded analytics; availability from union of overlapping outage windows; SLA targets need explicit sla_id",
        "sla.penalty.calc": "calculation-only with exact integer math; creates no charges; formula documented in result",
        "qos.measure": "evidence-based; integer averages (floor); default thresholds heuristic, override per call",
        "prov.allocate.resource": "atomic within root transaction; deterministic pick by (value,id); no live device binding",
        "prov.workorder.complete": "records operator-supplied evidence only; never marks network service active",
        "field.work.complete": "records operator-supplied evidence only; never marks network service active",
        "trouble.ticket.resolve": "records operator-supplied resolution evidence; external verification explicitly not performed",
        "general": "engines 1,2,3,11,12 local operations scope; all 22 unimplemented catalog IDs of these engines listed above with dependency",
    }
)

CATALOG_IDS_OWNED = frozenset(
    [
        "netinv.device.register", "netinv.device.update", "netinv.device.decommission", "netinv.hierarchy.build",
        "netinv.port.management", "netinv.link.management", "netinv.cable.trace", "netinv.spatial.view",
        "netinv.capacity.track", "netinv.utilization.track", "netinv.fiber.route", "netinv.splitter.management",
        "netinv.olt.port.alloc", "netinv.spare.parts", "netinv.audit.physical",
        "prov.order.create", "prov.order.validate", "prov.allocate.resource", "prov.configure.olt", "prov.configure.ont",
        "prov.activate.service", "prov.test.connectivity", "prov.suspend.service", "prov.unsuspend.service",
        "prov.terminate.service", "prov.change.plan", "prov.migrate.customer", "prov.relocate.service",
        "prov.workorder.create", "prov.workorder.complete",
        "noc.monitor.uptime", "noc.monitor.bandwidth", "noc.monitor.cpu", "noc.monitor.optical",
        "noc.monitor.temperature", "noc.alert.threshold", "noc.alert.fiber.cut", "noc.alert.outage",
        "noc.dashboard.realtime", "noc.map.network", "noc.ticket.create", "noc.ticket.assign",
        "noc.escalation.matrix", "noc.shift.handover", "noc.incident.postmortem",
        "field.dispatch.create", "field.dispatch.assign", "field.route.optimize", "field.arrival.track",
        "field.work.complete", "field.photo.upload", "field.signature.capture", "field.equipment.use",
        "field.time.track", "field.quality.score", "field.reassignment", "field.emergency.dispatch",
        "field.schedule.optimize", "field.skill.match", "field.feedback.collect",
        "sla.define", "sla.monitor", "sla.breach.alert", "sla.report.customer", "sla.penalty.calc",
        "qos.measure", "qos.threshold", "qos.speedtest.auto", "qos.benchmark",
        "trouble.ticket.create", "trouble.ticket.escalate", "trouble.ticket.resolve",
    ]
)

# Root-reviewed local lifecycle additions; none configures physical equipment.
SCHEMAS['netinv.olt.port.alloc']['properties']['action']={'type':'string','enum':['assign','release']}
LIMITATIONS['netinv.olt.port.alloc']='Local inventory assignment/release, including owned reservations; never activates/configures/disconnects real equipment.'

SCHEMAS['netinv.link.management']['properties']['link_id']=_S['id']
SCHEMAS['netinv.link.management']['properties']['status']={'type':'string','enum':['active','planned','inactive']}
LIMITATIONS['netinv.link.management']='Local topology records and status changes only; no real interface configuration.'

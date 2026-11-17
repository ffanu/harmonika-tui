#!/usr/bin/env python3
"""Harmonika Ops Module: Server admin + Network advanced + Code intelligence.

Adds 9 MCP tools to the Harmonika terminal:
  SERVER ADMIN:   server_health, log_analysis, process_hunter, service_manager
  NETWORK ADV:    network_discover, config_backup, firewall_analyzer
  CODE INTEL:     code_intelligence, test_runner

Pure stdlib + optional paramiko. Integrates into mcp_server.py via OPS_TOOLS.
"""
import ast
import fnmatch
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Reuse SSH exec from parent if available, else subprocess fallback
CONFIG_ROOT = os.environ.get('HARMONIKA_GLOBAL_CONFIG') or os.environ.get('HARMONIKA_CONFIG_DIR') or os.path.expanduser('~/.config/harmonika')
TERMINAL_ROOT = os.environ.get('HARMONIKA_TERMINAL_HOME') or os.path.expanduser('~/.config/harmonika-terminal')
NET_TARGETS_FILE = os.environ.get('HARMONIKA_NETWORK_TARGETS') or os.path.join(CONFIG_ROOT, 'network-targets.json')
CONFIG_BACKUP_DIR = os.path.join(CONFIG_ROOT,'config-backups')


def _load_targets():
    try:
        with open(NET_TARGETS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return [{'name': k, **v} for k, v in data.items()]
        return data
    except Exception:
        return []


def _find_target(name):
    targets = _load_targets()
    for t in targets:
        if t.get('name') == name or t.get('host') == name:
            return t
    return None


def _ssh_exec(target_name, command, timeout=30):
    """Execute command on target via net_ssh_exec.py helper."""
    target = _find_target(target_name)
    if not target:
        return {'ok': False, 'error': f'target not found: {target_name}'}
    req = {
        'host': target.get('host'),
        'port': int(target.get('port', 22)),
        'username': target.get('username', 'root'),
        'password': target.get('password', ''),
        'command': command,
        'timeout': timeout,
        'hostkey_sha256': target.get('hostkey_sha256', ''),
        'key_path': target.get('key_path', ''),
    }
    helper = os.path.join(os.path.dirname(__file__), 'net_ssh_exec.py')
    try:
        r = subprocess.run(
            [next((p for p in (os.environ.get('HARMONIKA_SSH_PYTHON'), os.path.join(TERMINAL_ROOT,'ssh-runtime',*(('Scripts','python.exe') if os.name=='nt' else ('bin','python'))), sys.executable) if p and os.path.isfile(p)), sys.executable), '-I', helper],
            input=json.dumps(req), capture_output=True, text=True, timeout=timeout + 10
        , encoding='utf-8', errors='replace')
        return json.loads(r.stdout.strip() or '{}')
    except Exception as e:
        return {'ok': False, 'error': 'ssh_helper_failed'}


def _parse_size(s):
    """Parse '1.5G', '500M', '1024K' to bytes."""
    s = str(s).strip().upper()
    m = re.match(r'([\d.]+)([KMGTP]?B?)', s)
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).rstrip('B')
    mult = {'': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4, 'P': 1024**5}
    return int(val * mult.get(unit, 1))


# ============ 1. SERVER HEALTH ============

def server_health(args):
    """All-in-one health check: CPU, RAM, disk, services, errors."""
    target = args.get('target', 'local')
    cmds = {
        'cpu': "uptime && nproc",
        'mem': "free -m",
        'disk': "df -h --output=source,fstype,size,used,avail,pcent,target -x tmpfs -x devtmpfs",
        'failed': "systemctl list-units --state=failed --no-legend 2>/dev/null | head -20",
        'errors': "journalctl -p err -n 15 --no-pager 2>/dev/null | tail -15",
        'top': "ps aux --sort=-%cpu | head -6",
        'net': "ip -br addr 2>/dev/null | grep -v lo",
        'load': "cat /proc/loadavg",
    }
    results = {}
    if target == 'local':
        for key, cmd in cmds.items():
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10, encoding='utf-8', errors='replace')
                results[key] = r.stdout.strip()
            except Exception as e:
                results[key] = f'error: {e}'
    else:
        combined = '; echo "---HARMONIKA-SEP---"; '.join(
            f'echo "### {k}"; {c}' for k, c in cmds.items()
        )
        r = _ssh_exec(target, combined, timeout=45)
        if not r.get('ok'):
            return {'ok': False, 'error': r.get('error', 'ssh failed')}
        raw = r.get('output', '')
        current = None
        for line in raw.split('\n'):
            m = re.match(r'### (\w+)', line.strip())
            if m:
                current = m.group(1)
                results[current] = []
            elif current and line.strip() and '---HARMONIKA-SEP---' not in line:
                results[current].append(line)
        results = {k: '\n'.join(v) if isinstance(v, list) else v for k, v in results.items()}

    # Health scoring
    issues = []
    health = 100
    try:
        load_parts = results.get('load', '0 0 0').split()
        load1 = float(load_parts[0]) if load_parts else 0
        ncpu = int(results.get('cpu', '0').strip().split()[-1]) if results.get('cpu') else 4
        if ncpu < 1:
            ncpu = 4
        if load1 > ncpu * 2:
            issues.append(f'CRITICAL: Load {load1} > 2x CPU ({ncpu})')
            health -= 30
        elif load1 > ncpu:
            issues.append(f'WARNING: Load {load1} > CPU count ({ncpu})')
            health -= 15
    except Exception:
        pass
    try:
        for line in results.get('mem', '').split('\n'):
            if line.startswith('Mem:'):
                parts = line.split()
                total, used = int(parts[1]), int(parts[2])
                pct = used * 100 // total if total else 0
                if pct > 90:
                    issues.append(f'CRITICAL: Memory {pct}% used')
                    health -= 25
                elif pct > 80:
                    issues.append(f'WARNING: Memory {pct}% used')
                    health -= 10
    except Exception:
        pass
    try:
        for line in results.get('disk', '').split('\n'):
            m = re.search(r'(\d+)%', line)
            if m and int(m.group(1)) > 90:
                issues.append(f'CRITICAL: Disk {m.group(1)}% full: {line.split()[-1]}')
                health -= 20
    except Exception:
        pass
    try:
        failed = [l for l in results.get('failed', '').split('\n') if l.strip() and '0 loaded' not in l]
        if failed:
            issues.append(f'WARNING: {len(failed)} failed services')
            health -= min(15, len(failed) * 3)
    except Exception:
        pass
    try:
        errors = [l for l in results.get('errors', '').split('\n') if l.strip()]
        if len(errors) > 10:
            issues.append(f'WARNING: {len(errors)} recent errors in journal')
            health -= 10
    except Exception:
        pass

    # Visual rendering (if viz module available)
    visual = None
    try:
        import viz_tools as _viz
        gauges = [
            _viz.gauge(load1 / ncpu * 100 if ncpu else 0, f'load {load1:.2f}/{ncpu}', 26),
            _viz.gauge(0, 'mem', 26), _viz.gauge(0, 'disk', 26),
        ]
        try:
            mem_lines = results.get('mem', '').split('\n')
            for ml in mem_lines:
                if ml.startswith('Mem:'):
                    mp = ml.split()
                    if len(mp) > 2 and int(mp[1]):
                        gauges[1] = _viz.gauge(int(mp[2]) / int(mp[1]) * 100, 'memory', 26)
            for dl in results.get('disk', '').split('\n'):
                dm = re.search(r'(\d+)%', dl)
                if dm:
                    gauges[2] = _viz.gauge(int(dm.group(1)), 'disk', 26)
                    break
        except Exception:
            pass
        visual = gauges
    except Exception:
        pass

    return {
        'ok': True,
        'target': target,
        'health_score': max(0, health),
        'status': 'HEALTHY' if health >= 85 else 'DEGRADED' if health >= 60 else 'CRITICAL',
        'issues': issues,
        'visual': visual,
        'details': {k: v[:500] for k, v in results.items()},
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    }


# ============ 2. LOG ANALYSIS ============

def log_analysis(args):
    """Analyze logs: extract errors, group patterns, find root causes."""
    target = args.get('target', 'local')
    service = args.get('service', '')
    query = args.get('query', '')
    lines = min(int(args.get('lines', 200)), 2000)
    log_file = args.get('file', '')

    if log_file:
        cmd = f'tail -n {lines} {log_file}'
    elif service:
        cmd = f'journalctl -u {service} -n {lines} --no-pager 2>/dev/null'
    else:
        cmd = f'journalctl -n {lines} --no-pager 2>/dev/null'

    if query:
        cmd += f' | grep -i "{query}"'

    if target == 'local':
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')
        raw = r.stdout
    else:
        r = _ssh_exec(target, cmd, timeout=35)
        if not r.get('ok'):
            return {'ok': False, 'error': r.get('error')}
        raw = r.get('output', '')

    lines_list = [l for l in raw.split('\n') if l.strip()]
    if not lines_list:
        return {'ok': True, 'message': 'no log lines found', 'total': 0}

    # Categorize
    error_pat = re.compile(r'\b(ERROR|ERR|FATAL|CRITICAL|PANIC|SEGV|OOM|killed|failed)\b', re.I)
    warn_pat = re.compile(r'\b(WARN|WARNING|DEPRECATED|CAUTION|RETRY)\b', re.I)
    errors, warns = [], []
    for l in lines_list:
        if error_pat.search(l):
            errors.append(l)
        elif warn_pat.search(l):
            warns.append(l)

    # Pattern grouping: normalize numbers, UUIDs, IPs for grouping
    def normalize(l):
        l = re.sub(r'\d{4}-\d{2}-\d{2}[T ][\d:.]+Z?( ?[+-]\d{4})?', '<TS>', l)
        l = re.sub(r'\b\d+(\.\d+)?\b', '<N>', l)
        l = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<UUID>', l, flags=re.I)
        l = re.sub(r'\b\d{1,3}(\.\d{1,3}){3}\b', '<IP>', l)
        return l[:200]

    from collections import Counter
    error_patterns = Counter(normalize(l) for l in errors).most_common(10)
    warn_patterns = Counter(normalize(l) for l in warns).most_common(5)

    # Extract stack traces
    traces = []
    in_trace = False
    for l in lines_list:
        if re.search(r'(Traceback|at [\w.$]+\(|#[\d]+ |Caused by)', l):
            in_trace = True
            traces.append(l)
        elif in_trace and (l.startswith('  ') or l.startswith('\t') or re.match(r'\w', l)):
            traces.append(l)
            if len(traces) > 50:
                break
        else:
            in_trace = False

    # Root cause suggestions
    suggestions = []
    err_text = ' '.join(errors[:50]).lower()
    rc_map = {
        'out of memory|oom': 'Memory exhaustion — check process memory limits, add swap, or scale up',
        'connection refused': 'Service not listening — check if target service is running and port is correct',
        'no space left': 'Disk full — clean logs/temp or expand storage',
        'permission denied': 'Access issue — check file permissions and user context',
        'timeout|timed out': 'Timeout — check network connectivity and service responsiveness',
        'certificate|ssl|tls': 'TLS issue — check cert expiry and trust chain',
        'dns|resolve': 'DNS failure — check /etc/resolv.conf and DNS server health',
        'segfault|signal 11': 'Crash — likely bug in application, check for core dumps',
        'too many open files': 'File descriptor exhaustion — increase ulimit or fix fd leak',
        'deadlock|lock wait': 'Lock contention — check database/application lock behavior',
    }
    for pat, suggestion in rc_map.items():
        if re.search(pat, err_text):
            suggestions.append(suggestion)

    return {
        'ok': True,
        'target': target,
        'service': service or log_file or 'system',
        'total_lines': len(lines_list),
        'error_count': len(errors),
        'warn_count': len(warns),
        'top_error_patterns': [{'pattern': p, 'count': c} for p, c in error_patterns],
        'top_warn_patterns': [{'pattern': p, 'count': c} for p, c in warn_patterns],
        'recent_errors': errors[:10],
        'stack_traces': traces[:30],
        'suggestions': suggestions,
    }


# ============ 3. PROCESS HUNTER ============

def process_hunter(args):
    """Find and manage processes: search by name, port, user. Actions: list, kill."""
    target = args.get('target', 'local')
    pattern = args.get('pattern', '')
    port = args.get('port')
    user = args.get('user', '')
    action = args.get('action', 'list')
    pid = args.get('pid')

    if action == 'kill':
        return {'ok':False,'error':'process_ownership_required','executed':False}

    # Search
    parts = []
    if pattern:
        parts.append(f"ps aux | grep -i '{pattern}' | grep -v grep")
    elif port:
        parts.append(f"ss -tlnp | grep ':{port} '")
    elif user:
        parts.append(f"ps aux -U {user}")
    else:
        parts.append("ps aux --sort=-%mem | head -20")
    cmd = ' ; '.join(parts)

    if target == 'local':
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
        out = r.stdout
    else:
        r = _ssh_exec(target, cmd, timeout=20)
        out = r.get('output', '')

    procs = []
    for line in out.split('\n'):
        if not line.strip() or line.startswith('USER') or 'PID' in line[:20]:
            continue
        parts_ = line.split(None, 10)
        if len(parts_) >= 11:
            procs.append({
                'user': parts_[0], 'pid': int(parts_[1]) if parts_[1].isdigit() else 0,
                'cpu': parts_[2], 'mem': parts_[3],
                'command': parts_[10][:150],
            })

    return {
        'ok': True,
        'target': target,
        'search': {'pattern': pattern, 'port': port, 'user': user},
        'count': len(procs),
        'processes': procs[:30],
        'hint': 'Use action=kill with pid to terminate a process',
    }


# ============ 4. SERVICE MANAGER ============

def service_manager(args):
    """Manage systemd services: status, start, stop, restart, logs."""
    target = args.get('target', 'local')
    service = args.get('service', '')
    action = args.get('action', 'status')

    if not service:
        # List running services
        cmd = "systemctl list-units --type=service --state=running --no-legend --no-pager | head -30"
        if target == 'local':
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10, encoding='utf-8', errors='replace')
            out = r.stdout
        else:
            r = _ssh_exec(target, cmd)
            out = r.get('output', '')
        services = []
        for line in out.split('\n'):
            if line.strip():
                name = line.split()[0].replace('.service', '')
                services.append(name)
        return {'ok': True, 'action': 'list', 'services': services}

    actions = {
        'status': f'systemctl status {service} --no-pager -l 2>&1 | head -25',
        'start': f'systemctl start {service} && sleep 1 && systemctl is-active {service}',
        'stop': f'systemctl stop {service} && echo stopped',
        'restart': f'systemctl restart {service} && sleep 2 && systemctl is-active {service}',
        'enable': f'systemctl enable {service} && echo enabled',
        'disable': f'systemctl disable {service} && echo disabled',
        'logs': f'journalctl -u {service} -n 50 --no-pager 2>&1 | tail -50',
    }
    cmd = actions.get(action)
    if not cmd:
        return {'ok': False, 'error': f'unknown action: {action}. Use: {list(actions.keys())}'}

    if target == 'local':
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')
        out = r.stdout + r.stderr
    else:
        r = _ssh_exec(target, cmd, timeout=35)
        out = r.get('output', '') + r.get('error', '')

    # Post-restart health check
    verify = None
    if action in ('start', 'restart'):
        vcmd = f'systemctl is-active {service} && systemctl show {service} -p NRestarts,ActiveState,SubState --no-pager'
        if target == 'local':
            vr = subprocess.run(vcmd, shell=True, capture_output=True, text=True, timeout=10, encoding='utf-8', errors='replace')
            verify = vr.stdout.strip()
        else:
            vr = _ssh_exec(target, vcmd)
            verify = vr.get('output', '').strip()

    return {
        'ok': True,
        'target': target,
        'service': service,
        'action': action,
        'output': out[:2000],
        'verify': verify,
    }


# ============ 5. NETWORK DISCOVER ============

def network_discover(args):
    """Discover devices: ping sweep, ARP, reverse DNS, common port check."""
    cidr = args.get('cidr', '')
    ports = args.get('ports', [22, 80, 443, 3389, 8291, 8728, 5900])
    deep = args.get('deep', False)

    if not cidr:
        # Use local subnet (macOS + Linux compatible)
        gateway = ''
        try:
            r = subprocess.run("ip route 2>/dev/null | grep default | awk '{print $3}' || route -n get default 2>/dev/null | grep gateway | awk '{print $2}'",
                             shell=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
            gateway = r.stdout.strip().split('\n')[0]
        except Exception:
            pass
        try:
            if not gateway:
                r = subprocess.run("netstat -nr 2>/dev/null | grep default | head -1 | awk '{print $2}'",
                                 shell=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
                gateway = r.stdout.strip()
        except Exception:
            pass
        if gateway and re.match(r'\d+\.\d+\.\d+\.\d+', gateway):
            cidr = gateway.rsplit('.', 1)[0] + '.0/24'
        else:
            # Fallback: get local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('8.8.8.8', 80))
                local_ip = s.getsockname()[0]
                cidr = local_ip.rsplit('.', 1)[0] + '.0/24'
            except Exception:
                cidr = '192.168.1.0/24'
            finally:
                s.close()

    # Parse CIDR (only support /24 for simplicity)
    base = cidr.split('/')[0].rsplit('.', 1)[0]
    hosts = []

    # ARP scan first (fast, no ping needed for local subnet)
    try:
        r = subprocess.run('arp -a', shell=True, capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
        for line in r.stdout.split('\n'):
            m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}).*?([0-9a-f]{1,2}(?::[0-9a-f]{1,2}){5})', line, re.I)
            if m and m.group(1).startswith(base):
                hosts.append({'ip': m.group(1), 'mac': m.group(2).lower()})
    except Exception:
        pass

    # Ping sweep in parallel
    if deep or not hosts:
        ping_procs = []
        for i in range(1, 255):
            ip = f'{base}.{i}'
            ping_procs.append((ip, subprocess.Popen(
                ['ping', '-c', '1', '-W', '1', '-q', ip],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )))
            if len(ping_procs) >= 50:  # batch
                for pip, p in ping_procs:
                    p.wait()
                ping_procs = []
        for pip, p in ping_procs:
            p.wait()

        # Re-check ARP after ping
        try:
            r = subprocess.run('arp -a', shell=True, capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
            seen_ips = {h['ip'] for h in hosts}
            for line in r.stdout.split('\n'):
                m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}).*?([0-9a-f]{1,2}(?::[0-9a-f]{1,2}){5})', line, re.I)
                if m and m.group(1).startswith(base) and m.group(1) not in seen_ips:
                    hosts.append({'ip': m.group(1), 'mac': m.group(2).lower()})
        except Exception:
            pass

    # Reverse DNS + port check + vendor guess
    # MAC OUI prefixes (common network vendors)
    oui_map = {
        'd4:ca:6d': 'MikroTik', 'cc:2d:e0': 'MikroTik', '48:8f:5a': 'MikroTik',
        '64:d1:54': 'MikroTik', 'e4:8d:8c': 'MikroTik', 'dc:2c:6e': 'MikroTik',
        '00:0c:29': 'VMware', '00:50:56': 'VMware', '52:54:00': 'QEMU/KVM',
        '00:1a:2b': 'Ayecom', 'b8:27:eb': 'Raspberry Pi', 'dc:a6:32': 'Raspberry Pi',
        '3c:ec:ef': 'Supermicro', '0c:c4:7a': 'Supermicro',
    }
    for h in hosts:
        mac = h.get('mac', '')
        h['vendor'] = 'Unknown'
        for prefix, vendor in oui_map.items():
            if mac.startswith(prefix):
                h['vendor'] = vendor
                break
        # Reverse DNS
        try:
            h['hostname'] = socket.gethostbyaddr(h['ip'])[0]
        except Exception:
            h['hostname'] = ''
        # Port check (top 3 only for speed)
        if deep:
            open_ports = []
            for port in ports[:6]:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                try:
                    s.connect((h['ip'], port))
                    open_ports.append(port)
                except Exception:
                    pass
                finally:
                    s.close()
            h['open_ports'] = open_ports
            if 8291 in open_ports or 8728 in open_ports:
                h['type'] = 'MikroTik Router'
            elif 3389 in open_ports:
                h['type'] = 'Windows/RDP'
            elif 5900 in open_ports:
                h['type'] = 'VNC Server'
            elif 22 in open_ports:
                h['type'] = 'Linux/SSH Server'

    topo_visual = None
    try:
        import viz_tools as _viz
        topo_visual = _viz.topology(hosts)
    except Exception:
        pass

    return {
        'ok': True,
        'cidr': cidr,
        'devices_found': len(hosts),
        'devices': hosts,
        'topology': topo_visual,
        'mikrotik_devices': [h for h in hosts if h.get('vendor') == 'MikroTik' or 8291 in h.get('open_ports', [])],
    }


# ============ 6. CONFIG BACKUP ============

def config_backup(args):
    """Backup device configs with versioning. Actions: backup, list, diff, restore."""
    os.makedirs(CONFIG_BACKUP_DIR, exist_ok=True)
    target = args.get('target', '')
    action = args.get('action', 'list')

    if action == 'list':
        backups = []
        for f in sorted(Path(CONFIG_BACKUP_DIR).glob('*.rsc'), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = f.stat()
            backups.append({
                'file': f.name,
                'target': f.stem.split('_')[0],
                'date': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'size': stat.st_size,
                'md5': hashlib.md5(f.read_bytes()).hexdigest()[:12],
            })
        return {'ok': True, 'backups': backups, 'location': CONFIG_BACKUP_DIR}

    if action == 'backup':
        if not target:
            return {'ok': False, 'error': 'target required'}
        t = _find_target(target)
        if not t:
            return {'ok': False, 'error': f'target not found: {target}'}
        dev_type = t.get('type', 'mikrotik')
        if dev_type == 'mikrotik':
            cmd = '/export show-sensitive'
        elif dev_type in ('vyos', 'vyatta'):
            cmd = 'show configuration commands'
        elif dev_type == 'cisco':
            cmd = 'show running-config'
        else:
            cmd = args.get('command', 'cat /etc/config/* 2>/dev/null || true')
        r = _ssh_exec(target, cmd, timeout=60)
        if not r.get('ok'):
            return {'ok': False, 'error': r.get('error')}
        config_text = r.get('output', '')
        if len(config_text) < 50:
            return {'ok': False, 'error': 'config too short, likely auth or command failed'}
        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        fname = f'{target}_{ts}.rsc'
        fpath = os.path.join(CONFIG_BACKUP_DIR, fname)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write('# Harmonika Config Backup\n# Target: %s\n# Date: %s\n# Type: %s\n\n' % (target, datetime.now().isoformat(), dev_type))
            f.write(config_text)
        return {
            'ok': True, 'action': 'backup', 'target': target,
            'file': fname, 'size': len(config_text),
            'md5': hashlib.md5(config_text.encode()).hexdigest()[:12],
        }

    if action == 'diff':
        file1 = args.get('file1', '')
        file2 = args.get('file2', '')
        if not (file1 and file2):
            # Use two latest
            files = sorted(Path(CONFIG_BACKUP_DIR).glob('*.rsc'), key=lambda p: p.stat().st_mtime, reverse=True)
            if len(files) < 2:
                return {'ok': False, 'error': 'need 2 files or at least 2 backups'}
            file1, file2 = files[1].name, files[0].name
        p1 = os.path.join(CONFIG_BACKUP_DIR, file1)
        p2 = os.path.join(CONFIG_BACKUP_DIR, file2)
        if not (os.path.exists(p1) and os.path.exists(p2)):
            return {'ok': False, 'error': 'file not found'}
        import difflib
        l1 = open(p1, encoding='utf-8').readlines()
        l2 = open(p2, encoding='utf-8').readlines()
        diff = list(difflib.unified_diff(l1, l2, fromfile=file1, tofile=file2, lineterm=''))
        added = len([d for d in diff if d.startswith('+') and not d.startswith('+++')])
        removed = len([d for d in diff if d.startswith('-') and not d.startswith('---')])
        return {
            'ok': True, 'file1': file1, 'file2': file2,
            'lines_added': added, 'lines_removed': removed,
            'diff': '\n'.join(diff[:100]),
        }

    if action == 'restore':
        return {'ok': False, 'error': 'restore requires manual approval. Use network_config tool to apply specific commands from backup.'}

    return {'ok': False, 'error': f'unknown action: {action}'}


# ============ 7. FIREWALL ANALYZER ============

def firewall_analyzer(args):
    """Analyze firewall rules: find conflicts, shadowed rules, permissive rules."""
    target = args.get('target', 'local')
    fw_type = args.get('type', '')

    if fw_type == 'mikrotik' or (target and target != 'local'):
        r = _ssh_exec(target, '/ip firewall filter print detail without-paging', timeout=30)
        if not r.get('ok'):
            return {'ok': False, 'error': r.get('error')}
        raw = r.get('output', '')
        rules = []
        current = {}
        for line in raw.split('\n'):
            if re.match(r'\s*\d+\s', line):
                if current:
                    rules.append(current)
                current = {'raw': line.strip()}
                m = re.search(r'chain=(\w+)', line)
                if m:
                    current['chain'] = m.group(1)
                m = re.search(r'action=(\w+)', line)
                if m:
                    current['action'] = m.group(1)
                for field in ('src-address', 'dst-address', 'protocol', 'dst-port', 'in-interface', 'comment'):
                    m = re.search(rf'{field}=([^\s]+)', line)
                    if m:
                        current[field] = m.group(1)
                m = re.search(r'disabled=(\w+)', line)
                current['disabled'] = m.group(1) == 'yes' if m else False
        if current:
            rules.append(current)
    else:
        r = subprocess.run('iptables -L -n -v --line-numbers 2>/dev/null', shell=True,
                         capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace')
        raw = r.stdout
        if not raw.strip():
            return {'ok': False, 'error': 'no firewall rules found (need root or use target for SSH)'}
        rules = []
        chain = ''
        for line in raw.split('\n'):
            if line.startswith('Chain'):
                chain = line.split()[1]
            elif re.match(r'^\d+\s', line):
                parts = line.split()
                rules.append({
                    'chain': chain, 'num': parts[0], 'pkts': parts[1], 'action': parts[2],
                    'raw': line[:150],
                })

    # Analysis
    issues = []
    active_rules = [r for r in rules if not r.get('disabled')]
    seen = {}
    for i, rule in enumerate(active_rules):
        key = f"{rule.get('chain','')}|{rule.get('action','')}|{rule.get('src-address','')}|{rule.get('dst-address','')}|{rule.get('dst-port','')}"
        if key in seen:
            issues.append({'type': 'DUPLICATE', 'rule': rule.get('raw', '')[:100],
                          'message': f'Rule duplicates #{seen[key]+1}'})
        else:
            seen[key] = i
        if rule.get('action') == 'accept':
            if not rule.get('src-address') and not rule.get('in-interface'):
                issues.append({'type': 'PERMISSIVE', 'rule': rule.get('raw', '')[:100],
                              'message': 'Accept rule with no source restriction'})
            if rule.get('dst-port') == '22' and not rule.get('src-address'):
                issues.append({'type': 'RISK', 'rule': rule.get('raw', '')[:100],
                              'message': 'SSH open to all sources'})
        if rule.get('action') == 'drop' and rule.get('chain') == 'input' and 'established' not in rule.get('raw', '').lower():
            pass  # complex shadowing analysis skipped
    # Shadowed rules: accept after drop for same criteria
    for i in range(len(active_rules) - 1):
        r1, r2 = active_rules[i], active_rules[i + 1]
        if (r1.get('action') == 'drop' and r2.get('action') == 'accept'
                and r1.get('src-address') == r2.get('src-address')
                and r1.get('dst-port') == r2.get('dst-port')
                and r1.get('chain') == r2.get('chain')):
            issues.append({'type': 'SHADOWED', 'rule': r2.get('raw', '')[:100],
                          'message': f'Rule is shadowed by drop rule #{i+1}, never matches'})

    return {
        'ok': True,
        'target': target,
        'total_rules': len(rules),
        'active_rules': len(active_rules),
        'disabled_rules': len(rules) - len(active_rules),
        'issues': issues,
        'rules_summary': {
            'accept': len([r for r in active_rules if r.get('action') == 'accept']),
            'drop': len([r for r in active_rules if r.get('action') == 'drop']),
            'reject': len([r for r in active_rules if r.get('action') == 'reject']),
        },
    }


# ============ 8. CODE INTELLIGENCE ============

def code_intelligence(args):
    """AST-aware code analysis: find definitions, references, structure, TODOs, imports."""
    action = args.get('action', 'structure')
    path = os.path.expanduser(args.get('path', '.'))
    query = args.get('query', '')
    lang = args.get('lang', 'python')

    if not os.path.exists(path):
        return {'ok': False, 'error': f'path not found: {path}'}

    if action == 'structure':
        # Show directory tree with file sizes
        tree = []
        if os.path.isfile(path):
            tree.append({'file': os.path.basename(path), 'size': os.path.getsize(path)})
        else:
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                           ('node_modules', '__pycache__', 'venv', '.git', 'dist', 'build', 'target')]
                rel = os.path.relpath(root, path)
                py_count = sum(1 for f in files if f.endswith('.py'))
                js_count = sum(1 for f in files if f.endswith(('.js', '.ts', '.jsx', '.tsx')))
                tree.append({'dir': rel, 'files': len(files), 'python': py_count, 'js': js_count})
                if len(tree) > 50:
                    break
        return {'ok': True, 'action': 'structure', 'path': path, 'tree': tree}

    if action == 'find_def':
        # Find function/class definitions using AST (Python) or grep (others)
        defs = []
        if lang == 'python':
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
                for f in files:
                    if not f.endswith('.py'):
                        continue
                    fpath = os.path.join(root, f)
                    try:
                        tree = ast.parse(open(fpath, encoding='utf-8').read())
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                                name = node.name
                                if not query or query.lower() in name.lower():
                                    sig = f"{name}({', '.join(ast.unparse(a) for a in node.args.args[:5])})"
                                    if hasattr(node, 'returns') and node.returns:
                                        sig += f" -> {ast.unparse(node.returns)}"
                                    defs.append({
                                        'name': name, 'type': type(node).__name__.replace('Def', '').replace('AsyncFunction', 'Function'),
                                        'file': os.path.relpath(fpath, path), 'line': node.lineno,
                                        'signature': sig[:120],
                                    })
                    except SyntaxError:
                        pass
        else:
            # grep for JS/TS/Go/Rust definitions
            patterns = {
                'javascript': r'(function\s+(\w+)|const\s+(\w+)\s*=\s*(async\s*)?\(|class\s+(\w+))',
                'go': r'func\s+\(?[\w\s*]*\)?\s*(\w+)',
                'rust': r'fn\s+(\w+)|struct\s+(\w+)|impl\s+(\w+)',
            }
            pat = patterns.get(lang, patterns['javascript'])
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                exts = {'javascript': ('.js', '.ts', '.jsx', '.tsx'), 'go': ('.go',), 'rust': ('.rs',)}
                for f in files:
                    if f.endswith(exts.get(lang, ('.js',))):
                        fpath = os.path.join(root, f)
                        for i, line in enumerate(open(fpath, errors='ignore', encoding='utf-8'), 1):
                            m = re.search(pat, line)
                            if m:
                                name = m.group(2) or m.group(3) or m.group(4) or m.group(1)
                                if not query or query.lower() in (name or '').lower():
                                    defs.append({'name': name, 'file': os.path.relpath(fpath, path), 'line': i, 'code': line.strip()[:120]})
        return {'ok': True, 'action': 'find_def', 'query': query, 'definitions': defs[:50], 'total': len(defs)}

    if action == 'find_ref':
        if not query:
            return {'ok': False, 'error': 'query required'}
        refs = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
            for f in files:
                if not f.endswith(('.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs')):
                    continue
                fpath = os.path.join(root, f)
                try:
                    for i, line in enumerate(open(fpath, errors='ignore', encoding='utf-8'), 1):
                        if query in line and f'def {query}' not in line and f'function {query}' not in line:
                            refs.append({'file': os.path.relpath(fpath, path), 'line': i, 'code': line.strip()[:120]})
                except Exception:
                    pass
        return {'ok': True, 'action': 'find_ref', 'query': query, 'references': refs[:50], 'total': len(refs)}

    if action == 'todos':
        todos = []
        pat = re.compile(r'//\s*(TODO|FIXME|HACK|XXX|BUG)|#\s*(TODO|FIXME|HACK|XXX|BUG)')
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
            for f in files:
                if not f.endswith(('.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs')):
                    continue
                fpath = os.path.join(root, f)
                for i, line in enumerate(open(fpath, errors='ignore', encoding='utf-8'), 1):
                    m = pat.search(line)
                    if m:
                        todos.append({'file': os.path.relpath(fpath, path), 'line': i,
                                     'type': m.group(1) or m.group(2), 'text': line.strip()[:120]})
        return {'ok': True, 'action': 'todos', 'todos': todos[:50], 'total': len(todos)}

    if action == 'imports':
        # Build import graph
        graph = {}
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
            for f in files:
                if not f.endswith('.py'):
                    continue
                fpath = os.path.join(root, f)
                rel = os.path.relpath(fpath, path)
                imports = []
                try:
                    tree = ast.parse(open(fpath, encoding='utf-8').read())
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            imports.extend(a.name for a in node.names)
                        elif isinstance(node, ast.ImportFrom) and node.module:
                            imports.append(node.module)
                except SyntaxError:
                    pass
                if imports:
                    graph[rel] = imports
        return {'ok': True, 'action': 'imports', 'graph': graph}

    return {'ok': False, 'error': f'unknown action: {action}. Use: structure, find_def, find_ref, todos, imports'}


# ============ 9. TEST RUNNER ============

def test_runner(args):
    """Auto-detect and run tests, parse failures, suggest fixes."""
    path = os.path.expanduser(args.get('path', '.'))
    framework = args.get('framework', 'auto')
    verbose = args.get('verbose', False)

    # Auto-detect framework
    if framework == 'auto':
        has = lambda f: os.path.exists(os.path.join(path, f))
        if has('pytest.ini') or has('pyproject.toml') or has('setup.py'):
            framework = 'pytest'
        elif has('package.json'):
            pkg = json.load(open(os.path.join(path, 'package.json'), encoding='utf-8')) if has('package.json') else {}
            scripts = pkg.get('scripts', {})
            if 'test' in scripts:
                if 'jest' in scripts.get('test', ''):
                    framework = 'jest'
                else:
                    framework = 'npm-test'
        elif has('go.mod'):
            framework = 'go'
        elif has('Cargo.toml'):
            framework = 'cargo'
        else:
            framework = 'pytest'

    commands = {
        'pytest': [sys.executable, '-m', 'pytest', path, '-v', '--tb=short', '-x'] if verbose else [sys.executable, '-m', 'pytest', path, '--tb=short', '-q'],
        'jest': [('npx.cmd' if os.name=='nt' else 'npx'), 'jest', '--no-coverage'],
        'npm-test': [('npm.cmd' if os.name=='nt' else 'npm'), 'test'],
        'go': ['go', 'test', './...'],
        'cargo': ['cargo', 'test'],
    }
    cmd = commands.get(framework)
    if not cmd:
        return {'ok': False, 'error': f'unsupported framework: {framework}'}

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=path, encoding='utf-8', errors='replace')
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'tests timed out (300s)'}
    except FileNotFoundError:
        return {'ok': False, 'error': f'command not found: {cmd[0]}'}

    # Parse results per framework
    result = {'ok': True, 'framework': framework, 'command': ' '.join(cmd), 'exit_code': r.returncode}
    if framework == 'pytest':
        result['passed'] = len(re.findall(r'PASSED|\.{1,1}(?=\.)', out))
        m = re.search(r'(\d+) passed', out)
        result['passed'] = int(m.group(1)) if m else 0
        m = re.search(r'(\d+) failed', out)
        result['failed'] = int(m.group(1)) if m else 0
        m = re.search(r'(\d+) error', out)
        result['errors'] = int(m.group(1)) if m else 0
        failures = re.findall(r'_{5,}\s*(\w[\w./]+)::(\w+)\s*_{5,}\n(.*?)(?=_{5,}|=|$)', out, re.DOTALL)
        result['failing_tests'] = [{'file': f, 'test': t, 'error': e.strip()[:500]} for f, t, e in failures[:10]]
    elif framework in ('jest', 'npm-test'):
        m = re.search(r'(\d+) passing', out)
        result['passed'] = int(m.group(1)) if m else 0
        m = re.search(r'(\d+) failing', out)
        result['failed'] = int(m.group(1)) if m else 0
        failures = re.findall(r'●\s*(\S+)\s*(.*?)(?=●|$)', out, re.DOTALL)
        result['failing_tests'] = [{'test': t, 'error': e.strip()[:500]} for t, e in failures[:10]]
    elif framework == 'go':
        result['passed'] = len(re.findall(r'^--- PASS', out, re.M))
        result['failed'] = len(re.findall(r'^--- FAIL', out, re.M))
        failures = re.findall(r'--- FAIL: (\S+)\s*(.*?)(?=--- |ok |FAIL|$)', out, re.DOTALL | re.M)
        result['failing_tests'] = [{'test': t, 'error': e.strip()[:300]} for t, e in failures[:10]]
    elif framework == 'cargo':
        m = re.search(r'(\d+) passed', out)
        result['passed'] = int(m.group(1)) if m else 0
        result['failed'] = out.count('FAILED')

    # Suggestions based on error types
    suggestions = []
    err_lower = out.lower()
    if 'importerror' in err_lower or 'modulenotfound' in err_lower:
        suggestions.append('Missing dependency — run pip install / npm install')
    if 'assertionerror' in err_lower:
        suggestions.append('Logic mismatch — check expected vs actual values in failing tests')
    if 'timeout' in err_lower:
        suggestions.append('Timeout issue — check async operations, network mocks, or slow fixtures')
    if 'connection refused' in err_lower:
        suggestions.append('Service not running — ensure test dependencies (DB, Redis) are started')
    if 'typeerror' in err_lower:
        suggestions.append('Type mismatch — check function signatures and data shapes')
    result['suggestions'] = suggestions
    result['output_tail'] = out[-2000:] if len(out) > 2000 else out
    return result


# ============ REGISTRY ============

OPS_TOOLS = {
    'server_health': {
        'description': 'All-in-one server health check: CPU load, memory, disk, failed services, recent errors. Returns health score 0-100 with issue list.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'target': {'type': 'string', 'description': 'Target name from network-targets.json or "local" (default)'},
            },
        },
    },
    'log_analysis': {
        'description': 'Analyze server logs: extract errors, group by pattern, detect stack traces, suggest root causes. Works with journalctl or log files.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'target': {'type': 'string', 'description': 'Target or "local"'},
                'service': {'type': 'string', 'description': 'Systemd service name (e.g. nginx)'},
                'file': {'type': 'string', 'description': 'Log file path (alternative to service)'},
                'query': {'type': 'string', 'description': 'Filter keyword'},
                'lines': {'type': 'number', 'description': 'Lines to analyze (default 200, max 2000)'},
            },
        },
    },
    'process_hunter': {
        'description': 'Find processes by name, port, or user. Shows PID, CPU, memory, command. Can kill by PID.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'target': {'type': 'string'},
                'pattern': {'type': 'string', 'description': 'Process name pattern'},
                'port': {'type': 'number', 'description': 'Find process listening on port'},
                'user': {'type': 'string', 'description': 'Filter by user'},
                'action': {'type': 'string', 'enum': ['list', 'kill'], 'description': 'list or kill'},
                'pid': {'type': 'number', 'description': 'PID to kill (for action=kill)'},
                'force': {'type': 'boolean', 'description': 'Use kill -9'},
            },
        },
    },
    'service_manager': {
        'description': 'Manage systemd services: status, start, stop, restart, enable, disable, logs. With post-action verification.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'target': {'type': 'string'},
                'service': {'type': 'string', 'description': 'Service name (empty = list all running)'},
                'action': {'type': 'string', 'enum': ['status', 'start', 'stop', 'restart', 'enable', 'disable', 'logs', 'list']},
            },
        },
    },
    'network_discover': {
        'description': 'Discover network devices: ARP scan, ping sweep, reverse DNS, vendor detection (MikroTik/VMware/etc), port check. Finds routers, servers, IoT.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'cidr': {'type': 'string', 'description': 'Subnet CIDR e.g. 192.168.1.0/24 (default: auto-detect)'},
                'deep': {'type': 'boolean', 'description': 'Full ping sweep + port scan (slower)'},
                'ports': {'type': 'array', 'items': {'type': 'number'}, 'description': 'Ports to check'},
            },
        },
    },
    'config_backup': {
        'description': 'Backup network device configs (MikroTik/VyOS/Cisco) with versioning, list backups, diff between versions.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string', 'enum': ['backup', 'list', 'diff']},
                'target': {'type': 'string', 'description': 'Device name from network-targets.json'},
                'file1': {'type': 'string'},
                'file2': {'type': 'string'},
            },
        },
    },
    'firewall_analyzer': {
        'description': 'Analyze firewall rules: find duplicates, shadowed rules, overly permissive accepts, SSH exposure. Supports MikroTik and iptables.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'target': {'type': 'string', 'description': 'Device name (MikroTik) or "local" (iptables)'},
                'type': {'type': 'string', 'enum': ['mikrotik', 'iptables']},
            },
        },
    },
    'code_intelligence': {
        'description': 'AST-aware code analysis: find function/class definitions, find references, directory structure, TODOs/FIXMEs, import graph. Python (AST) + JS/TS/Go/Rust.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string', 'enum': ['structure', 'find_def', 'find_ref', 'todos', 'imports']},
                'path': {'type': 'string', 'description': 'File or directory path'},
                'query': {'type': 'string', 'description': 'Search query (symbol name)'},
                'lang': {'type': 'string', 'enum': ['python', 'javascript', 'go', 'rust']},
            },
        },
    },
    'test_runner': {
        'description': 'Auto-detect and run tests (pytest/jest/go/cargo), parse results, extract failures, suggest fixes.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'Project path'},
                'framework': {'type': 'string', 'description': 'auto (default), pytest, jest, npm-test, go, cargo'},
                'verbose': {'type': 'boolean'},
            },
        },
    },
}

OPS_HANDLERS = {
    'server_health': server_health,
    'log_analysis': log_analysis,
    'process_hunter': process_hunter,
    'service_manager': service_manager,
    'network_discover': network_discover,
    'config_backup': config_backup,
    'firewall_analyzer': firewall_analyzer,
    'code_intelligence': code_intelligence,
    'test_runner': test_runner,
}

# Local system administration above uses POSIX tools. Do not report fabricated
# success when running that code on Windows; remote SSH targets remain usable.
def _portable_ops(name, handler):
    def invoke(args):
        if os.name == 'nt' and args.get('target','local') in ('local','localhost','',None) and name not in ('code_intelligence','test_runner'):
            return {'ok':False,'error':'unsupported_local_platform','platform':'windows','operation':name,'executed':False,'remote_target_supported':True}
        return handler(args)
    return invoke
OPS_HANDLERS={name:_portable_ops(name,handler) for name,handler in OPS_HANDLERS.items()}

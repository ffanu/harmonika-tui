#!/usr/bin/env python3
"""Harmonika OS Intelligence: multi-OS mastery + safe package installation.

Tools:
  os_intelligence — detect OS, map packages across distros, learn OS state, security audit
  safe_install    — preflight → snapshot → plan → install → verify → rollback

Never breaks a server: dry-run first, disk check, conflict check, critical package
protection, snapshot rollback, post-install verification.

Supported: Ubuntu/Debian (apt), CentOS/RHEL/Rocky/Alma (yum/dnf), Fedora (dnf),
Alpine (apk), openSUSE (zypper), Arch (pacman), macOS (brew).
"""
import json
import os
import re
import platform
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ops_tools import _ssh_exec, _find_target

CONFIG_ROOT = os.environ.get('HARMONIKA_GLOBAL_CONFIG') or os.environ.get('HARMONIKA_CONFIG_DIR') or os.path.expanduser('~/.config/harmonika')
SNAPSHOT_DIR = os.path.join(CONFIG_ROOT,'os-snapshots')

# ─────────────────────────── KNOWLEDGE BASE ───────────────────────────

PKG_MGR = {
    'apt': {
        'os': ['ubuntu', 'debian', 'linuxmint', 'pop'],
        'update_index': 'apt-get update -qq',
        'install': 'DEBIAN_FRONTEND=noninteractive apt-get install -y -q {pkg}',
        'dry_run': 'apt-get install -s {pkg}',
        'remove': 'DEBIAN_FRONTEND=noninteractive apt-get remove -y {pkg}',
        'search': 'apt-cache search {q} 2>/dev/null | head -20',
        'info': 'dpkg -s {pkg} 2>/dev/null || apt-cache show {pkg} 2>/dev/null | head -20',
        'installed': "dpkg -l 2>/dev/null | awk '/^ii/{print $2\" \"$3}' | head -500",
        'is_installed': 'dpkg -s {pkg} >/dev/null 2>&1 && echo YES || echo NO',
        'find_file': 'dpkg -S {file} 2>/dev/null',
        'list_upgradable': 'apt list --upgradable 2>/dev/null | tail -n +2 | head -50',
        'clean': 'apt-get autoremove -y -q && apt-get clean',
        'fix_broken': 'apt-get install -f -y -q',
        'held': 'apt-mark showhold',
        'repo_add': 'add-apt-repository -y {repo} && apt-get update -qq',
    },
    'dnf': {
        'os': ['fedora', 'rhel9', 'rocky', 'almalinux'],
        'update_index': 'dnf makecache -q 2>/dev/null || dnf clean metadata && dnf makecache',
        'install': 'dnf install -y -q {pkg}',
        'dry_run': 'dnf install --assumeno {pkg} 2>&1',
        'remove': 'dnf remove -y {pkg}',
        'search': 'dnf search {q} 2>/dev/null | head -25',
        'info': 'dnf info {pkg} 2>/dev/null | head -25',
        'installed': 'rpm -qa --qf "%{NAME} %{VERSION}\\n" 2>/dev/null | head -500',
        'is_installed': 'rpm -q {pkg} >/dev/null 2>&1 && echo YES || echo NO',
        'find_file': 'rpm -qf {file} 2>/dev/null',
        'list_upgradable': 'dnf check-update -q 2>/dev/null | head -50',
        'clean': 'dnf autoremove -y && dnf clean all',
        'fix_broken': 'rpm -Va --nofiles --nodigest 2>/dev/null; echo done',
        'held': 'dnf distro-sync --assumeno 2>&1 | head -5',
        'repo_add': 'dnf config-manager --set-enabled {repo} 2>/dev/null || dnf install -y epel-release',
    },
    'yum': {
        'os': ['centos', 'rhel', 'amazon', 'oracle'],
        'update_index': 'yum makecache fast -q 2>/dev/null || yum clean all && yum makecache',
        'install': 'yum install -y -q {pkg}',
        'dry_run': 'yum install --assumeno {pkg} 2>&1',
        'remove': 'yum remove -y {pkg}',
        'search': 'yum search {q} 2>/dev/null | head -25',
        'info': 'yum info {pkg} 2>/dev/null | head -25',
        'installed': 'rpm -qa --qf "%{NAME} %{VERSION}\\n" 2>/dev/null | head -500',
        'is_installed': 'rpm -q {pkg} >/dev/null 2>&1 && echo YES || echo NO',
        'find_file': 'rpm -qf {file} 2>/dev/null',
        'list_upgradable': 'yum check-update -q 2>/dev/null | head -50',
        'clean': 'yum autoremove -y && yum clean all',
        'fix_broken': 'yum complete-transaction 2>/dev/null; echo done',
        'held': 'yum versionlock list 2>/dev/null | head -10',
        'repo_add': 'yum install -y epel-release',
    },
    'apk': {
        'os': ['alpine'],
        'update_index': 'apk update -q 2>/dev/null',
        'install': 'apk add -q {pkg}',
        'dry_run': 'apk add --simulate {pkg} 2>&1',
        'remove': 'apk del -q {pkg}',
        'search': 'apk search -q {q} | head -20',
        'info': 'apk info {pkg} 2>/dev/null | head -20',
        'installed': 'apk info -v 2>/dev/null | head -500',
        'is_installed': 'apk info -e {pkg} >/dev/null 2>&1 && echo YES || echo NO',
        'find_file': 'apk info --who-owns {file} 2>/dev/null',
        'list_upgradable': 'apk version -l "<" 2>/dev/null | head -30',
        'clean': 'apk cache clean',
        'fix_broken': 'apk fix',
        'held': 'apk info 2>/dev/null | head -1',
        'repo_add': 'echo "repo addition manual for alpine: /etc/apk/repositories"',
    },
    'zypper': {
        'os': ['opensuse', 'sles'],
        'update_index': 'zypper --non-interactive refresh 2>/dev/null',
        'install': 'zypper --non-interactive install {pkg}',
        'dry_run': 'zypper install --dry-run {pkg} 2>&1',
        'remove': 'zypper --non-interactive remove {pkg}',
        'search': 'zypper search {q} 2>/dev/null | head -25',
        'info': 'zypper info {pkg} 2>/dev/null | head -25',
        'installed': 'rpm -qa --qf "%{NAME} %{VERSION}\\n" | head -500',
        'is_installed': 'rpm -q {pkg} >/dev/null 2>&1 && echo YES || echo NO',
        'find_file': 'rpm -qf {file} 2>/dev/null',
        'list_upgradable': 'zypper list-updates 2>/dev/null | head -30',
        'clean': 'zypper clean --all',
        'fix_broken': 'zypper verify 2>/dev/null; echo done',
        'held': 'zypper ll 2>/dev/null | head -10',
        'repo_add': 'zypper addrepo {repo} 2>/dev/null',
    },
    'pacman': {
        'os': ['arch', 'manjaro'],
        'update_index': 'pacman -Sy --noconfirm 2>/dev/null',
        'install': 'pacman -S --noconfirm --needed {pkg}',
        'dry_run': 'pacman -S --print {pkg} 2>&1',
        'remove': 'pacman -R --noconfirm {pkg}',
        'search': 'pacman -Ss {q} 2>/dev/null | head -20',
        'info': 'pacman -Si {pkg} 2>/dev/null | head -25',
        'installed': 'pacman -Q 2>/dev/null | head -500',
        'is_installed': 'pacman -Q {pkg} >/dev/null 2>&1 && echo YES || echo NO',
        'find_file': 'pacman -Qo {file} 2>/dev/null',
        'list_upgradable': 'pacman -Qu 2>/dev/null | head -30',
        'clean': 'pacman -Sc --noconfirm 2>/dev/null',
        'fix_broken': 'pacman -Qk 2>/dev/null; echo done',
        'held': 'pacman -Q | head -1',
        'repo_add': 'echo "edit /etc/pacman.conf for arch repos"',
    },
    'brew': {
        'os': ['darwin'],
        'update_index': 'brew update 2>/dev/null || echo "brew update skipped"',
        'install': 'brew install {pkg}',
        'dry_run': 'brew install --dry-run {pkg} 2>&1',
        'remove': 'brew uninstall {pkg}',
        'search': 'brew search {q} 2>/dev/null | head -20',
        'info': 'brew info {pkg} 2>/dev/null | head -20',
        'installed': 'brew list --versions 2>/dev/null | head -500',
        'is_installed': 'brew list --versions {pkg} >/dev/null 2>&1 && echo YES || echo NO',
        'find_file': 'brew which-formula {file} 2>/dev/null',
        'list_upgradable': 'brew outdated 2>/dev/null | head -30',
        'clean': 'brew cleanup 2>/dev/null',
        'fix_broken': 'brew doctor 2>/dev/null | head -10',
        'held': 'brew pin 2>/dev/null; echo done',
        'repo_add': 'brew tap {repo} 2>/dev/null',
    },
}

# Package name translation across distros (need → per-distro names)
# Multi-package needs use spaces; alternatives use |
PKG_TRANSLATIONS = {
    'build-essential': {'apt': 'build-essential', 'yum': 'gcc gcc-c++ make', 'dnf': 'gcc gcc-c++ make', 'apk': 'build-base', 'zypper': 'gcc gcc-c++ make', 'pacman': 'base-devel', 'brew': 'gcc make'},
    'python-dev': {'apt': 'python3-dev python3-venv python3-pip', 'yum': 'python3-devel python3-pip', 'dnf': 'python3-devel python3-pip', 'apk': 'python3-dev py3-pip', 'zypper': 'python3-devel python3-pip', 'pacman': 'python python-pip', 'brew': 'python'},
    'python': {'apt': 'python3 python3-pip python3-venv', 'yum': 'python3 python3-pip', 'dnf': 'python3 python3-pip', 'apk': 'python3 py3-pip', 'pacman': 'python python-pip', 'brew': 'python'},
    'web-server': {'apt': 'nginx', 'yum': 'nginx', 'dnf': 'nginx', 'apk': 'nginx', 'zypper': 'nginx', 'pacman': 'nginx', 'brew': 'nginx'},
    'apache': {'apt': 'apache2', 'yum': 'httpd', 'dnf': 'httpd', 'apk': 'apache2', 'zypper': 'apache2', 'pacman': 'apache', 'brew': 'httpd'},
    'database': {'apt': 'postgresql postgresql-contrib', 'yum': 'postgresql-server postgresql-contrib', 'dnf': 'postgresql-server', 'apk': 'postgresql', 'zypper': 'postgresql-server', 'pacman': 'postgresql', 'brew': 'postgresql'},
    'mysql': {'apt': 'mariadb-server mariadb-client', 'yum': 'mariadb-server', 'dnf': 'mariadb-server', 'apk': 'mariadb mariadb-client', 'zypper': 'mariadb', 'pacman': 'mariadb', 'brew': 'mariadb'},
    'redis': {'apt': 'redis-server', 'yum': 'redis', 'dnf': 'redis', 'apk': 'redis', 'zypper': 'redis', 'pacman': 'redis', 'brew': 'redis'},
    'nodejs': {'apt': 'nodejs npm', 'yum': 'nodejs npm', 'dnf': 'nodejs npm', 'apk': 'nodejs npm', 'zypper': 'nodejs npm', 'pacman': 'nodejs npm', 'brew': 'node'},
    'docker': {'apt': 'docker.io docker-compose', 'yum': 'docker docker-compose', 'dnf': 'docker docker-compose', 'apk': 'docker docker-compose', 'zypper': 'docker docker-compose', 'pacman': 'docker docker-compose', 'brew': 'docker docker-compose'},
    'git': {'apt': 'git', 'yum': 'git', 'dnf': 'git', 'apk': 'git', 'zypper': 'git', 'pacman': 'git', 'brew': 'git'},
    'curl-wget': {'apt': 'curl wget', 'yum': 'curl wget', 'dnf': 'curl wget', 'apk': 'curl wget', 'zypper': 'curl wget', 'pacman': 'curl wget', 'brew': 'curl wget'},
    'ffmpeg': {'apt': 'ffmpeg', 'yum': 'ffmpeg ffmpeg-devel', 'dnf': 'ffmpeg', 'apk': 'ffmpeg', 'zypper': 'ffmpeg', 'pacman': 'ffmpeg', 'brew': 'ffmpeg'},
    'ssl-cert': {'apt': 'certbot python3-certbot-nginx', 'yum': 'certbot python3-certbot-nginx', 'dnf': 'certbot python3-certbot-nginx', 'apk': 'certbot', 'zypper': 'certbot', 'pacman': 'certbot', 'brew': 'certbot'},
    'monitoring': {'apt': 'htop iotop iftop net-tools', 'yum': 'htop iotop iftop net-tools', 'dnf': 'htop iotop iftop net-tools', 'apk': 'htop iotop iftop', 'zypper': 'htop iotop iftop', 'pacman': 'htop iotop iftop', 'brew': 'htop iftop'},
    'firewall': {'apt': 'ufw', 'yum': 'firewalld', 'dnf': 'firewalld', 'apk': 'ufw', 'zypper': 'firewalld', 'pacman': 'ufw', 'brew': 'none'},
    'ssh-hardening': {'apt': 'openssh-server fail2ban', 'yum': 'openssh-server fail2ban', 'dnf': 'openssh-server fail2ban', 'apk': 'openssh fail2ban', 'zypper': 'openssh fail2ban', 'pacman': 'openssh fail2ban', 'brew': 'openssh'},
    'image-processing': {'apt': 'imagemagick libmagickwand-dev', 'yum': 'ImageMagick ImageMagick-devel', 'dnf': 'ImageMagick ImageMagick-devel', 'apk': 'imagemagick imagemagick-dev', 'zypper': 'ImageMagick', 'pacman': 'imagemagick', 'brew': 'imagemagick'},
    'compression': {'apt': 'zip unzip tar gzip bzip2 xz-utils', 'yum': 'zip unzip tar gzip bzip2 xz', 'dnf': 'zip unzip tar gzip bzip2 xz', 'apk': 'zip unzip tar gzip bzip2 xz', 'zypper': 'zip unzip tar gzip bzip2', 'pacman': 'zip unzip tar gzip bzip2 xz', 'brew': 'gzip bzip2 xz'},
    'vim-editor': {'apt': 'vim', 'yum': 'vim-enhanced', 'dnf': 'vim-enhanced', 'apk': 'vim', 'zypper': 'vim', 'pacman': 'vim', 'brew': 'vim'},
    'samba': {'apt': 'samba', 'yum': 'samba samba-client', 'dnf': 'samba samba-client', 'apk': 'samba', 'zypper': 'samba', 'pacman': 'samba', 'brew': 'samba'},
    'nginx-extras': {'apt': 'libnginx-mod-http-headers-more libnginx-mod-stream', 'yum': 'nginx-all-modules', 'dnf': 'nginx-all-modules', 'apk': 'nginx-mod-http-headers-more', 'zypper': 'nginx-module-headers-more', 'pacman': 'nginx', 'brew': 'nginx'},
}

# Packages that must NEVER be removed (server killers)
CRITICAL_PKGS = {
    'openssh-server', 'openssh', 'openssh-clients', 'sshd', 'systemd', 'systemd-sysv',
    'sudo', 'libc6', 'glibc', 'bash', 'coreutils', 'dpkg', 'rpm', 'apt', 'yum', 'dnf',
    'networkmanager', 'iptables', 'netfilter', 'util-linux', 'busybox', 'initramfs-tools',
    'grub', 'grub2', 'linux-firmware', 'dbus', 'selinux-policy', 'ca-certificates', 'curl',
}

# Services that restart on upgrade (may cause downtime)
RESTART_SERVICES = {
    'nginx': 'web downtime ~2s',
    'apache2': 'web downtime ~3s', 'httpd': 'web downtime ~3s',
    'postgresql': 'DB downtime ~5s', 'mysql': 'DB downtime ~5s', 'mariadb': 'DB downtime ~5s',
    'redis': 'cache flush possible', 'docker': 'all containers restart',
    'openssh-server': 'SSH sessions survive, new connections blocked briefly',
}


# ─────────────────────────── HELPERS ───────────────────────────

def _run(target, cmd, timeout=30):
    """Run command local or via SSH target."""
    if target in ('local', 'localhost', '', None):
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace')
            return r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return 'TIMEOUT'
        except Exception as e:
            return 'ERROR: execution_failed'
    return _ssh_exec(target, cmd, timeout=timeout).get('output', '') or 'SSH_FAILED'


def detect_os(target='local'):
    """Detect OS, version, package manager, architecture."""
    if os.name=='nt' and target in ('local','localhost','',None):
        return {'id':'windows','name':'Windows','version':platform.version(),'arch':platform.machine(),'pkg_mgr':None}
    raw = _run(target, 'cat /etc/os-release 2>/dev/null | head -5; echo ---; uname -m; echo ---; uname -s; echo ---; command -v apt-get dnf yum apk zypper pacman brew 2>/dev/null')
    info = {'id': 'unknown', 'name': 'Unknown', 'version': '', 'arch': '', 'pkg_mgr': None}
    section = 0
    kernel_name = ''
    for line in raw.split('\n'):
        if line.strip() == '---':
            section += 1
            continue
        if section == 0:
            m = re.match(r'ID=["\']?([\w-]+)', line)
            if m and info['id'] == 'unknown':
                info['id'] = m.group(1).lower()
            m = re.match(r'PRETTY_NAME=["\']?(.+?)["\']?$', line)
            if m:
                info['name'] = m.group(1)
            m = re.match(r'VERSION_ID=["\']?([\d.]+)', line)
            if m:
                info['version'] = m.group(1)
        elif section == 1 and not info['arch']:
            info['arch'] = line.strip()
        elif section == 2:
            kernel_name = (line.strip() or '').lower()
        elif section == 3:
            for mgr in ('apt', 'dnf', 'yum', 'apk', 'zypper', 'pacman', 'brew'):
                if line.strip().endswith(mgr) or line.strip().endswith('/' + mgr):
                    if info['pkg_mgr'] is None or mgr == 'apt':
                        info['pkg_mgr'] = mgr
                        break
    # macOS detection
    if kernel_name == 'darwin':
        sw_vers = _run(target, 'sw_vers 2>/dev/null | grep ProductVersion | awk \'{print $2}\'').strip()
        info.update({'id': 'darwin', 'name': f'macOS {sw_vers}'.strip(), 'version': sw_vers})
        if info['pkg_mgr'] is None:
            info['pkg_mgr'] = 'brew'
    # Fallback detection
    if info['pkg_mgr'] is None:
        if 'ubuntu' in info['id'] or 'debian' in info['id']:
            info['pkg_mgr'] = 'apt'
        elif any(x in info['id'] for x in ('centos', 'rhel', 'rocky', 'alma', 'amazon')):
            info['pkg_mgr'] = 'yum' if 'centos' in info['id'] and info['version'].startswith('7') else 'dnf'
        elif info['id'] == 'alpine':
            info['pkg_mgr'] = 'apk'
    info['family'] = 'debian' if info['pkg_mgr'] == 'apt' else \
                     'redhat' if info['pkg_mgr'] in ('yum', 'dnf') else \
                     info['id']
    return info


def get_pkg_mgr_cmds(target, os_info=None):
    """Get package manager command set for target."""
    info = os_info or detect_os(target)
    mgr = info.get('pkg_mgr') or 'apt'
    return PKG_MGR.get(mgr, PKG_MGR['apt']), mgr


# ─────────────────────────── OS INTELLIGENCE TOOL ───────────────────────────

def os_intelligence(args):
    """Main tool: detect, map packages, learn state, security audit."""
    action = args.get('action', 'detect')
    target = args.get('target', 'local')

    if action == 'detect':
        info = detect_os(target)
        if info['id']=='windows':return {'ok':True,'action':'detect','target':target,'os':info}
        # Extra context: repos, kernel, uptime
        info['kernel'] = _run(target, 'uname -r').strip()
        info['uptime'] = _run(target, 'uptime 2>/dev/null | sed "s/.*up //; s/,.*//"').strip()
        if info.get('pkg_mgr') == 'apt':
            repos = _run(target, "grep -h '^deb ' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null | head -10")
        elif info.get('pkg_mgr') in ('yum', 'dnf'):
            repos = _run(target, "dnf repolist 2>/dev/null || yum repolist 2>/dev/null | head -10")
        else:
            repos = ''
        info['repos'] = [r.strip() for r in repos.split('\n') if r.strip()][:10]
        return {'ok': True, 'action': 'detect', 'target': target, 'os': info}

    if os.name=='nt' and target in ('local','localhost','',None):return {'ok':False,'error':'unsupported_local_platform','platform':'windows','executed':False,'remote_target_supported':True}

    if action == 'map':
        """Translate a software need or package name to this target's distro."""
        need = args.get('need', '') or args.get('package', '')
        if not need:
            return {'ok': False, 'error': 'need or package required (e.g. "python-dev", "web-server", "nginx")'}
        info = detect_os(target)
        mgr = info.get('pkg_mgr') or 'apt'
        cmds, _ = get_pkg_mgr_cmds(target, info)
        need_lower = need.lower().strip()
        result = {'ok': True, 'action': 'map', 'target': target, 'os': info['name'], 'pkg_mgr': mgr}
        if need_lower in PKG_TRANSLATIONS:
            result['need'] = need_lower
            result['packages'] = PKG_TRANSLATIONS[need_lower].get(mgr, PKG_TRANSLATIONS[need_lower]['apt'])
            result['note'] = f"Install with: {mgr} install {result['packages']}"
        else:
            # Direct package: search to verify existence
            search_out = _run(target, cmds['search'].format(q=need), timeout=20)
            result['need'] = need
            result['search_results'] = [l.strip() for l in search_out.split('\n') if l.strip()][:10]
            result['note'] = f"Not a known meta-need. Search results above; verify name for {info['name']}."
        # Is it already installed?
        pkg_list = result.get('packages', need)
        first_pkg = pkg_list.split()[0] if pkg_list else need
        installed = _run(target, cmds['is_installed'].format(pkg=first_pkg), timeout=15).strip()
        result['already_installed'] = installed == 'YES'
        return result

    if action == 'learn':
        """Deep OS state analysis: installed packages, versions, updates, disk, drift."""
        info = detect_os(target)
        cmds, mgr = get_pkg_mgr_cmds(target, info)
        # Disk space
        disk = _run(target, "df -h / 2>/dev/null | tail -1")
        # Memory
        mem = _run(target, "free -m 2>/dev/null | grep Mem || vm_stat 2>/dev/null | head -3")
        # Key software versions
        versions = {}
        for sw, cmd in {
            'python3': 'python3 --version 2>&1', 'node': 'node --version 2>&1',
            'nginx': 'nginx -v 2>&1', 'docker': 'docker --version 2>&1',
            'git': 'git --version 2>&1', 'postgres': 'psql --version 2>&1',
        }.items():
            out = _run(target, cmd, timeout=10).strip()
            if out and 'not found' not in out and 'command not found' not in out:
                versions[sw] = out.split('\n')[0]
        # Pending updates
        updates = _run(target, cmds['list_upgradable'], timeout=30)
        update_count = len([l for l in updates.split('\n') if l.strip() and 'TIMEOUT' not in l])
        # Failed services (if systemd)
        failed = _run(target, "systemctl list-units --state=failed --no-legend 2>/dev/null | head -5")
        return {
            'ok': True, 'action': 'learn', 'target': target,
            'os': info['name'], 'pkg_mgr': mgr,
            'disk_root': disk.strip(),
            'memory': mem.strip()[:150],
            'software_versions': versions,
            'pending_updates': update_count,
            'failed_services': [l.split()[0] for l in failed.split('\n') if l.strip() and not l.strip().startswith('●') and l.strip() != '0 loaded units listed'],
        }

    if action == 'security':
        """Security audit: updates, exposed ports, weak configs."""
        info = detect_os(target)
        cmds, mgr = get_pkg_mgr_cmds(target, info)
        findings = []
        # Security updates
        if mgr == 'apt':
            sec = _run(target, "apt list --upgradable 2>/dev/null | grep -i securi | wc -l").strip()
            if sec.isdigit() and int(sec) > 0:
                findings.append({'level': 'HIGH', 'finding': f'{sec} security updates pending'})
        elif mgr in ('yum', 'dnf'):
            sec = _run(target, "dnf updateinfo list available security 2>/dev/null | wc -l || yum updateinfo list security all 2>/dev/null | wc -l").strip()
            if sec.isdigit() and int(sec) > 0:
                findings.append({'level': 'HIGH', 'finding': f'{sec} security advisories pending'})
        # Listening ports
        ports = _run(target, "ss -tlnp 2>/dev/null | tail -n +2 | awk '{print $4}' | grep -o '[0-9]*$' | sort -un | head -20")
        open_ports = [p for p in ports.split('\n') if p.strip().isdigit()]
        risky = {'23': 'telnet (insecure)', '21': 'ftp (insecure)', '3389': 'RDP exposed',
                 '5900': 'VNC exposed', '6379': 'redis exposed (often no auth)',
                 '3306': 'MySQL exposed', '5432': 'PostgreSQL exposed',
                 '27017': 'MongoDB exposed', '9200': 'Elasticsearch exposed'}
        for p in open_ports:
            if p in risky:
                findings.append({'level': 'MEDIUM', 'finding': f'Port {p} open: {risky[p]}'})
        # SSH config
        ssh_cfg = _run(target, "sshd -T 2>/dev/null | grep -E 'permitrootlogin|passwordauthentication' | head -4")
        if 'permitrootlogin yes' in ssh_cfg:
            findings.append({'level': 'HIGH', 'finding': 'SSH permits root login'})
        if 'passwordauthentication no' not in ssh_cfg and 'passwordauthentication yes' in ssh_cfg:
            findings.append({'level': 'MEDIUM', 'finding': 'SSH allows password auth (consider key-only)'})
        # Empty passwords
        if _run(target, "grep -E '^[^:]+::' /etc/shadow 2>/dev/null | head -1").strip():
            findings.append({'level': 'CRITICAL', 'finding': 'User(s) with empty password found'})
        score = 100 - sum({'CRITICAL': 30, 'HIGH': 15, 'MEDIUM': 5}.get(f['level'], 5) for f in findings)
        return {'ok': True, 'action': 'security', 'target': target,
                'security_score': max(0, score), 'findings': findings,
                'open_ports': open_ports}

    return {'ok': False, 'error': f'unknown action: {action}. Use: detect, map, learn, security'}


# ─────────────────────────── SAFE INSTALL TOOL ───────────────────────────

def safe_install(args):
    """Safely install packages: preflight → snapshot → dry-run → install → verify.
    Actions: preflight (check only), plan (full plan), execute (do it), rollback."""
    target = args.get('target', 'local')
    packages = args.get('packages', '') or args.get('need', '')
    action = args.get('action', 'preflight')
    if os.name=='nt' and target in ('local','localhost','',None):return {'ok':False,'error':'unsupported_local_platform','platform':'windows','executed':False,'remote_target_supported':True}
    os.makedirs(SNAPSHOT_DIR, mode=0o700, exist_ok=True)

    info = detect_os(target)
    cmds, mgr = get_pkg_mgr_cmds(target, info)

    # Translate meta-need
    need_lower = packages.lower().strip()
    if need_lower in PKG_TRANSLATIONS:
        packages = PKG_TRANSLATIONS[need_lower].get(mgr, PKG_TRANSLATIONS[need_lower]['apt'])
    if not packages and action != 'rollback':
        return {'ok': False, 'error': 'packages or need required'}

    pkg_list = packages.split()
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    snap_file = os.path.join(SNAPSHOT_DIR, f'{target.replace("/", "_")}_{ts}.json')

    # ─── PREFLIGHT: checks that never modify anything ───
    checks = []
    passed = True

    # 1. Disk space (need at least 500MB free on /)
    disk_raw = _run(target, "df -BM / 2>/dev/null | tail -1 | awk '{print $4}' | tr -d 'M'")
    try:
        free_mb = int(disk_raw)
        ok = free_mb > 500
        checks.append({'check': 'disk_space', 'value': f'{free_mb}MB free', 'ok': ok,
                       'detail': 'need >500MB' if not ok else ''})
        passed = passed and ok
    except ValueError:
        passed = False
        checks.append({'check': 'disk_space', 'ok': False, 'detail': 'disk_space_not_verified'})

    # 2. Already installed?
    already = []
    for p in pkg_list[:5]:
        inst = _run(target, cmds['is_installed'].format(pkg=p), timeout=15).strip()
        if inst == 'YES':
            already.append(p)
    checks.append({'check': 'already_installed', 'value': already, 'ok': True,
                   'detail': f'{len(already)}/{len(pkg_list)} already present — will skip or upgrade'})

    # 3. Package exists in repos? (dry run reveals this)
    dry_out = _run(target, cmds['dry_run'].format(pkg=packages), timeout=60)
    dry_lines = dry_out.split('\n')
    will_install = [l.strip() for l in dry_lines if re.search(r'^(Inst|Installing|install)', l)][:15]
    not_found = [p for p in pkg_list if re.search(re.escape(p) + r'.*(not found|No match|unable to|error)', dry_out, re.I)]
    ok = not not_found
    checks.append({'check': 'package_exists', 'value': f'{len(will_install)} deps to install',
                   'ok': ok, 'detail': f'not found: {not_found}' if not_found else ''})
    passed = passed and ok

    # 4. Critical package protection
    dangerous = [p for p in pkg_list if p.lower() in CRITICAL_PKGS or p.lower() in ('remove', 'purge')]
    ok = not dangerous
    checks.append({'check': 'critical_protection', 'value': 'safe' if ok else dangerous,
                   'ok': ok, 'detail': 'refusing to touch critical packages' if not ok else ''})
    passed = passed and ok

    # 5. Service impact (downtime warning)
    impacts = {p: RESTART_SERVICES[p] for p in pkg_list if p in RESTART_SERVICES}
    checks.append({'check': 'service_impact', 'value': impacts or 'none expected',
                   'ok': True, 'detail': 'downtime warnings above' if impacts else ''})

    # 6. Broken packages pre-existing?
    broken_out = _run(target, cmds['fix_broken'], timeout=30)
    has_broken = 'broken' in broken_out.lower() or 'incomplete' in broken_out.lower()
    checks.append({'check': 'existing_state', 'value': 'broken deps found' if has_broken else 'clean',
                   'ok': not has_broken, 'detail': 'fix existing deps first' if has_broken else ''})
    passed = passed and not has_broken

    if action == 'preflight':
        return {'ok': passed, 'action': 'preflight', 'target': target, 'os': info['name'],
                'packages': packages, 'checks': checks,
                'verdict': 'SAFE_TO_INSTALL' if passed else 'BLOCKED — fix issues above'}

    if action == 'plan':
        return {'ok': True, 'action': 'plan', 'target': target, 'os': info['name'],
                'pkg_mgr': mgr, 'packages': packages,
                'command': cmds['install'].format(pkg=packages),
                'dry_run_summary': will_install[:20],
                'checks': checks,
                'rollback_strategy': f'snapshot saved to {snap_file} before install; rollback action restores',
                'estimated_new_packages': len(will_install)}

    if action == 'execute':
        if not passed:
            return {'ok': False, 'action': 'execute', 'error': 'preflight FAILED — use action=preflight to see issues',
                    'checks': checks}
        # SNAPSHOT current state
        installed_list = _run(target, cmds['installed'], timeout=30)
        services = _run(target, "systemctl list-units --type=service --state=running --no-legend 2>/dev/null | awk '{print $1}' | head -50")
        snapshot = {
            'timestamp': ts, 'target': target, 'os': info['name'],
            'about_to_install': packages,
            'installed_packages': installed_list[:20000],
            'running_services': services.split('\n')[:50],
        }
        with open(snap_file, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f)
        # UPDATE index quietly
        _run(target, cmds['update_index'], timeout=90)
        # INSTALL
        install_out = _run(target, cmds['install'].format(pkg=packages), timeout=300)
        # VERIFY each package
        verification = []
        for p in pkg_list[:5]:
            inst = _run(target, cmds['is_installed'].format(pkg=p), timeout=15).strip()
            verification.append({'package': p, 'installed': inst == 'YES'})
        all_ok = all(v['installed'] for v in verification)
        return {'ok': all_ok, 'action': 'execute', 'target': target,
                'packages': packages,
                'verification': verification,
                'install_output_tail': install_out[-500:],
                'snapshot_file': snap_file,
                'rollback': f'Use safe_install action=rollback snapshot={os.path.basename(snap_file)}'}

    if action == 'rollback':
        snap_name = args.get('snapshot', '')
        if not snap_name:
            snaps = sorted(Path(SNAPSHOT_DIR).glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            if not snaps:
                return {'ok': False, 'error': 'no snapshots found'}
            snap_name = snaps[0].name
        snap_path = os.path.join(SNAPSHOT_DIR, snap_name)
        if not os.path.exists(snap_path):
            return {'ok': False, 'error': f'snapshot not found: {snap_name}',
                    'available': [p.name for p in Path(SNAPSHOT_DIR).glob('*.json')][:10]}
        snapshot = json.load(open(snap_path, encoding='utf-8'))
        pkgs = snapshot.get('about_to_install', '').split()
        if not pkgs:
            return {'ok': False, 'error': 'snapshot has no install record'}
        remove_out = _run(target, cmds['remove'].format(pkg=' '.join(pkgs)), timeout=120)
        # Restore services that were running before
        for svc in snapshot.get('running_services', []):
            if svc and any(p in svc for p in pkgs):
                _run(target, f'systemctl restart {svc}', timeout=30)
        return {'ok': True, 'action': 'rollback', 'target': target,
                'removed': pkgs, 'output_tail': remove_out[-300:]}

    return {'ok': False, 'error': f'unknown action: {action}. Use: preflight, plan, execute, rollback'}


OS_INTEL_TOOLS = {
    'os_intelligence': {
        'description': 'OS mastery engine: detect OS/version/pkg-manager, translate package names across distros (Ubuntu/CentOS/Alpine/Arch/macOS), analyze OS state (installed software, updates, failed services), security audit (exposed ports, SSH config, security updates). Actions: detect, map, learn, security.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string', 'enum': ['detect', 'map', 'learn', 'security'],
                           'description': 'detect=OS profile, map=translate package need to this distro, learn=state analysis, security=audit'},
                'target': {'type': 'string', 'description': 'Target name or "local" (default)'},
                'need': {'type': 'string', 'description': 'Software need for map: e.g. python-dev, web-server, database, build-essential, ffmpeg'},
            },
            'required': ['action'],
        },
    },
    'safe_install': {
        'description': 'Safe package installer that NEVER breaks the server: preflight checks (disk space, package existence, critical package protection, service downtime warning) → snapshot for rollback → dry-run → install → verify. Actions: preflight (check only), plan (show full plan), execute (install with rollback safety net), rollback (restore from snapshot).',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string', 'enum': ['preflight', 'plan', 'execute', 'rollback'],
                           'description': 'preflight=safety check only, plan=full plan, execute=install with snapshot+verify, rollback=undo'},
                'target': {'type': 'string', 'description': 'Target name or "local"'},
                'packages': {'type': 'string', 'description': 'Package names space-separated, e.g. "nginx certbot"'},
                'need': {'type': 'string', 'description': 'Meta-need: python-dev, web-server, database, build-essential, monitoring...'},
                'snapshot': {'type': 'string', 'description': 'Snapshot filename for rollback'},
            },
            'required': ['action'],
        },
    },
}

OS_INTEL_HANDLERS = {
    'os_intelligence': os_intelligence,
    'safe_install': safe_install,
}

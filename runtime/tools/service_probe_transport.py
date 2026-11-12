"""Bounded transport for a trusted service probe, independent of shell quoting growth."""
import base64
import re
import shlex
import zlib


def build_probe_command(script, service, lines=80):
    if not isinstance(service,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,120}\.service',service):
        raise ValueError('invalid_service')
    if type(lines) is not int or not 1<=lines<=200:
        raise ValueError('invalid_journal_lines')
    return build_fixed_probe(script,[service,str(lines)])


def build_fixed_probe(script, args=()):
    if not isinstance(script,str) or len(script.encode('utf-8'))>20000:
        raise ValueError('service_probe_size_limit')
    if not isinstance(args,(list,tuple)) or len(args)>8 or any(not isinstance(x,str) or len(x)>128 or '\x00' in x for x in args):
        raise ValueError('invalid_probe_arguments')
    encoded=base64.b64encode(zlib.compress(script.encode('utf-8'),9)).decode('ascii')
    runner='import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('+repr(encoded)+')),"<harmonika-service-probe>","exec"))'
    command='python3 -I -c '+shlex.quote(runner)+''.join(' '+shlex.quote(x) for x in args)
    if len(command)>8192:raise ValueError('service_probe_transport_size_limit')
    return command

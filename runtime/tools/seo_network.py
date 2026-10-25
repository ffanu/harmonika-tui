"""SSRF-safe bounded HTTP client for Harmonika SEO. Python 3.9 stdlib only.

Enforced for every request and every redirect hop:

- http/https only, ports 80/443 only; no userinfo, fragments, or control
  characters; internal hostnames (localhost/.local/.internal/...) rejected.
- Literal and DNS-resolved addresses must all be globally routable. Loopback,
  private, link-local, multicast, unspecified, reserved, IPv4-mapped IPv6,
  6to4 and Teredo wrapped addresses are rejected.
- The first validated resolved address is pinned onto the raw socket. The HTTP
  Host header, TLS SNI and certificate verification keep using the URL
  hostname, so no second DNS lookup can reroute the connection (DNS rebinding).
- Every redirect Location is fully revalidated, at most 4 hops are followed,
  and Authorization is never forwarded: a redirected authenticated request is
  rejected outright. Authorization may only come from the trusted adapter that
  constructs the call, never from user input.
- Responses are size-capped, never decompressed (Accept-Encoding: identity is
  always sent, compressed replies are rejected) and decoded with a bounded
  charset allowlist.
- Per-request socket timeout, a global wall-clock budget and a request-count
  budget cover the whole fetch including redirects. POST is never retried and
  response data is returned as text, never executed.
- Proxy environment variables are ignored; connections go directly to the
  pinned address. All error messages are redacted and static: no raw URLs,
  hosts, tokens, or operating system errors ever reach the exception text.

The DNS resolver and connection factory are instance methods, so tests inject
fakes via unittest.mock without opening real network connections.
"""
import http.client
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, quote, parse_qsl


class NetworkError(Exception):
    """Redacted network failure. code is a stable identifier, message is static."""

    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


MAX_URL_LENGTH = 2048
MAX_HOST_LENGTH = 253
MAX_REDIRECTS = 4
DEFAULT_MAX_REQUESTS = 8
DEFAULT_MAX_BYTES = 2097152
DEFAULT_BUDGET_SECONDS = 22
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_USER_AGENT = 'HarmonikaSEO'
READ_CHUNK = 65536
FORBIDDEN_HOSTS = frozenset({
    'localhost', 'local', 'internal', 'localdomain', 'ip6-localhost', 'ip6-loopback',
})
FORBIDDEN_SUFFIXES = ('.localhost', '.local', '.internal', '.localdomain', '.lan', '.home', '.home.arpa')
HOST_LABEL = re.compile(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$')
NUMERIC_LABEL = re.compile(r'^(?:0[xX][0-9a-fA-F]+|[0-9]+)$')
MANAGED_HEADERS = frozenset({
    'host', 'content-length', 'connection', 'transfer-encoding', 'accept-encoding',
    'te', 'upgrade', 'proxy-authorization', 'proxy-connection', 'keep-alive',
    'expect', 'trailer',
})
SAFE_CHARSETS = frozenset({
    'utf-8', 'utf8', 'ascii', 'us-ascii', 'iso-8859-1', 'latin-1', 'cp1252',
    'windows-1252',
})


def _is_global_address(address):
    """True only for direct globally routable unicast addresses; reject address tunnels."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo is not None:
            return False
    if address.is_multicast or address.is_reserved or address.is_unspecified \
            or address.is_loopback or address.is_link_local or address.is_private:
        return False
    return address.is_global


def _validate_host(hostname):
    if '%' in hostname:raise NetworkError('invalid_host','Scoped addresses are not allowed')
    if not hostname:
        raise NetworkError('invalid_url', 'URL has no host')
    if hostname in FORBIDDEN_HOSTS or hostname.endswith(FORBIDDEN_SUFFIXES):
        raise NetworkError('forbidden_host', 'Internal hostnames are not allowed')
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        if not _is_global_address(address):
            raise NetworkError('non_global_ip', 'Only globally routable IP literals are allowed')
        return hostname
    if hostname.isascii():
        name = hostname.lower()
    else:
        try:
            name = hostname.encode('idna').decode('ascii').lower()
        except (UnicodeError, ValueError):
            raise NetworkError('invalid_host', 'Hostname is not valid') from None
    labels = name.split('.')
    if len(labels)<2 and not NUMERIC_LABEL.fullmatch(name):raise NetworkError('forbidden_host','Fully qualified public hostname required')
    if len(name) > MAX_HOST_LENGTH or any(not HOST_LABEL.fullmatch(label) for label in labels):
        raise NetworkError('invalid_host', 'Hostname is not valid')
    if all(NUMERIC_LABEL.fullmatch(label) for label in labels):
        raise NetworkError('invalid_host', 'Ambiguous numeric hostnames are not allowed')
    return name


def validate_url(url):
    """Validate an absolute http(s) URL and return its normalized ASCII form."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise NetworkError('invalid_url', 'URL must be a non-empty string of reasonable length')
    for character in url:
        if character == ' ' or ord(character) < 0x20 or ord(character) == 0x7f:
            raise NetworkError('invalid_character', 'URL contains control characters')
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise NetworkError('invalid_url', 'URL could not be parsed') from None
    if parts.scheme not in ('http', 'https'):
        raise NetworkError('scheme_not_allowed', 'Only http and https URLs are allowed')
    if parts.fragment:
        raise NetworkError('fragment_not_allowed', 'URL fragments are not allowed')
    if parts.username is not None or parts.password is not None or '@' in parts.netloc:
        raise NetworkError('userinfo_not_allowed', 'URL userinfo is not allowed')
    default_port = 80 if parts.scheme == 'http' else 443
    if port is not None and port != default_port:
        raise NetworkError('port_not_allowed', 'Only ports 80 and 443 are allowed')
    hostname = _validate_host((parts.hostname or '').rstrip('.'))
    netloc = '[' + hostname + ']' if ':' in hostname else hostname
    return urlunsplit((parts.scheme, netloc, quote(parts.path or '/',safe='/%:@!$&\'()*+,;=-._~'), quote(parts.query,safe='/%?:@!$&\'()*+,;=-._~'), ''))


def _decode_text(data, content_type):
    charset = None
    for parameter in content_type.split(';')[1:]:
        name, _, value = parameter.strip().partition('=')
        if name.strip().lower() == 'charset':
            charset = value.strip().strip('"').lower()
            break
    if charset not in SAFE_CHARSETS:
        charset = 'utf-8'
    return data.decode(charset, errors='replace')


class SafeFetcher:
    """Bounded SSRF-safe fetcher. Resolver and connection factory are injectable."""

    def __init__(self, max_requests=DEFAULT_MAX_REQUESTS, max_bytes=DEFAULT_MAX_BYTES,
                 budget_seconds=DEFAULT_BUDGET_SECONDS, timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                 clock=time.monotonic):
        if max_requests < 1 or max_bytes < 1 or budget_seconds <= 0 or timeout_seconds <= 0:
            raise NetworkError('invalid_configuration', 'Fetcher bounds must be positive')
        self.max_requests = int(max_requests)
        self.max_bytes = int(max_bytes)
        self.budget_seconds = float(budget_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self._clock = clock
        self._deadline = None
        self._requests_used = 0

    def fetch(self, url, method='GET', headers=None, body=None):
        """Fetch one URL with redirects. Returns {url, status, headers, text, content_type}."""
        request_method = self._check_method(method)
        request_headers, payload = self._prepare_request(headers, request_method, body)
        if self._deadline is None:self._deadline=self._clock()+self.budget_seconds
        deadline = self._deadline
        current = validate_url(url)
        redirects = 0
        while True:
            if self._requests_used >= self.max_requests:
                raise NetworkError('request_budget_exhausted', 'Request count budget exhausted')
            now = self._clock()
            if now >= deadline:
                raise NetworkError('budget_exceeded', 'Wall-clock budget exhausted')
            timeout = min(self.timeout_seconds, deadline - now)
            if timeout <= 0:
                raise NetworkError('budget_exceeded', 'Wall-clock budget exhausted')
            parts = urlsplit(current)
            hostname = (parts.hostname or '').rstrip('.')
            https = parts.scheme == 'https'
            port = 443 if https else 80
            self._requests_used += 1
            address = self._pin_address(hostname)
            connection = self._open_connection(address, port, hostname, https, timeout)
            target = parts.path or '/'
            if parts.query:
                target = target + '?' + parts.query
            try:
                connection.request(request_method, target, body=payload, headers=dict(request_headers))
                response = connection.getresponse()
                status = response.status
                response_headers = {
                    str(name).lower(): str(value) for name, value in response.headers.items()
                }
                encoding = response_headers.get('content-encoding', '').strip().lower()
                if encoding not in ('', 'identity'):
                    raise NetworkError('unsupported_compression', 'Compressed responses are rejected')
                declared = response_headers.get('content-length', '').strip()
                if declared.isdigit() and int(declared) > self.max_bytes:
                    raise NetworkError('response_too_large', 'Response exceeds the byte budget')
                data = self._read_bounded(response, deadline)
            except NetworkError:
                raise
            except (socket.timeout, TimeoutError):
                raise NetworkError('request_timeout', 'The request timed out') from None
            except (OSError, ssl.SSLError, http.client.HTTPException):
                raise NetworkError('network_failed', 'The request could not be completed') from None
            finally:
                try:
                    connection.close()
                except OSError:
                    pass
            if 300 <= status < 400:
                location = response_headers.get('location', '').strip()
                if location:
                    redirects += 1
                    if redirects > MAX_REDIRECTS:
                        raise NetworkError('too_many_redirects', 'Too many redirect hops')
                    if any(name.lower() == 'authorization' for name in request_headers):
                        raise NetworkError(
                            'authenticated_redirect_rejected',
                            'Authenticated requests are never redirected',
                        )
                    if any(k.lower() in {'key','api_key','access_token','token','client_secret'} for k,v in parse_qsl(urlsplit(current).query)):
                        raise NetworkError('credential_redirect_rejected','Requests containing credential query parameters are not redirected')
                    if request_method=='POST':raise NetworkError('post_redirect_rejected','POST requests are never replayed across redirects')
                    current = validate_url(urljoin(current, location))
                    if status in (301, 302, 303):
                        request_method = 'GET'
                        payload = None
                        for name in [n for n in request_headers if n.lower() == 'content-type']:
                            del request_headers[name]
                    continue
            content_type = response_headers.get('content-type', '')
            return {
                'url': current,
                'status': status,
                'headers': response_headers,
                'body_bytes': data,
                'text': _decode_text(data, content_type),
                'content_type': content_type.split(';')[0].strip().lower(),
            }

    __call__ = fetch

    @staticmethod
    def _check_method(method):
        if not isinstance(method, str) or method.upper() not in ('GET', 'POST'):
            raise NetworkError('method_not_allowed', 'Only GET and POST are allowed')
        return method.upper()

    def _prepare_request(self, headers, method, body):
        merged = {
            'User-Agent': DEFAULT_USER_AGENT,
            'Accept-Encoding': 'identity',
            'Connection': 'close',
        }
        if headers is not None:
            if not isinstance(headers, dict):
                raise NetworkError('invalid_header', 'Headers must be a string mapping')
            for name, value in headers.items():
                if not isinstance(name, str) or not isinstance(value, str):
                    raise NetworkError('invalid_header', 'Header names and values must be strings')
                if name.strip().lower() in MANAGED_HEADERS:
                    raise NetworkError('invalid_header', 'Managed headers cannot be overridden')
                if any(ord(c) < 0x20 or ord(c) == 0x7f for c in name + value):
                    raise NetworkError('invalid_header', 'Header line breaks are not allowed')
                merged[name.strip()] = value.strip()
        payload = None
        if body is not None:
            if method != 'POST':
                raise NetworkError('body_not_allowed', 'Only POST requests carry a body')
            if isinstance(body, str):
                payload = body.encode('utf-8')
            elif isinstance(body, (bytes, bytearray)):
                payload = bytes(body)
            else:
                raise NetworkError('invalid_body', 'Body must be text or bytes')
            if len(payload) > self.max_bytes:
                raise NetworkError('request_body_too_large', 'Request body exceeds the byte budget')
            if not any(name.lower() == 'content-type' for name in merged):
                merged['Content-Type'] = 'application/x-www-form-urlencoded'
        return merged, payload

    def _pin_address(self, hostname):
        """Resolve once, require every address to be public, and pin the first.

        Only the pinned address is used for the socket, so a second DNS lookup
        (DNS rebinding) can never reroute the request to another host.
        """
        try:
            addresses = self._resolve_host(hostname)
        except NetworkError:
            raise
        except OSError:
            raise NetworkError('dns_failure', 'DNS resolution failed') from None
        seen = []
        for value in addresses:
            text = str(value)
            if text not in seen:
                seen.append(text)
        if not seen:
            raise NetworkError('dns_failure', 'DNS returned no addresses')
        for text in seen:
            try:
                address = ipaddress.ip_address(text)
            except ValueError:
                raise NetworkError('dns_failure', 'DNS returned an invalid address') from None
            if not _is_global_address(address):
                raise NetworkError('non_global_ip', 'DNS resolved to a non-public address')
        return seen[0]

    def _resolve_host(self, hostname):
        return [info[4][0] for info in socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)]

    def _tls_context(self):
        return ssl.create_default_context()

    def _open_connection(self, address, port, hostname, https, timeout):
        """Connect the socket to the pinned address; verify TLS against hostname."""
        try:
            raw = socket.create_connection((address, port), timeout=timeout)
        except OSError:
            raise NetworkError('connect_failed', 'The connection could not be established') from None
        if https:
            try:
                sock = self._tls_context().wrap_socket(raw, server_hostname=hostname)
            except (OSError, ssl.SSLError):
                try:
                    raw.close()
                except OSError:
                    pass
                raise NetworkError('tls_failed', 'The TLS certificate could not be verified') from None
            connection = http.client.HTTPSConnection(hostname, port, timeout=timeout)
        else:
            sock = raw
            connection = http.client.HTTPConnection(hostname, port, timeout=timeout)
        connection.sock = sock
        return connection

    def _read_bounded(self, response, deadline):
        chunks = []
        total = 0
        while True:
            if self._clock() >= deadline:
                raise NetworkError('budget_exceeded', 'Wall-clock budget exhausted')
            piece = response.read(min(READ_CHUNK, self.max_bytes - total + 1))
            if not piece:
                return b''.join(chunks)
            total += len(piece)
            if total > self.max_bytes:
                raise NetworkError('response_too_large', 'Response exceeds the byte budget')
            chunks.append(piece)

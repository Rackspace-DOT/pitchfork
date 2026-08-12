"""
URL safety validation functions for SSRF protection.

This module contains dependency-light functions for validating outbound URLs
to prevent Server-Side Request Forgery (SSRF) attacks. These functions have
no Flask or application-specific dependencies, making them easy to test
and reuse.
"""

import re
import socket

try:
    from urllib.parse import urlsplit, urlunsplit
except Exception:
    from urlparse import urlsplit, urlunsplit


class UnsafeOutboundRequest(ValueError):
    """Raised when an outbound URL fails safety validation."""
    pass


def _has_userinfo(parsed_url):
    """Check if a parsed URL contains userinfo (username/password)."""
    return bool(
        getattr(parsed_url, 'username', None) or
        getattr(parsed_url, 'password', None) or
        '@' in parsed_url.netloc
    )


def _is_private_ipv4(ip):
    """
    Check if an IPv4 address is private/reserved.

    Returns True for private, loopback, link-local, current network,
    multicast, and reserved IPv4 ranges.
    """
    try:
        packed = socket.inet_aton(ip)
    except Exception:
        return False

    octets = [
        octet if isinstance(octet, int) else ord(octet)
        for octet in packed
    ]
    return (
        octets[0] == 10 or
        octets[0] == 127 or
        (octets[0] == 169 and octets[1] == 254) or
        (octets[0] == 172 and 16 <= octets[1] <= 31) or
        (octets[0] == 192 and octets[1] == 168) or
        (octets[0] == 0) or
        octets[0] >= 224
    )


def _is_private_ipv6(ip):
    """
    Check if an IPv6 address is private/reserved.

    Returns True for loopback, unspecified, link-local, unique local,
    IPv4-mapped private, and IPv4-compatible private addresses.
    """
    try:
        packed = socket.inet_pton(socket.AF_INET6, ip)
    except Exception:
        return False

    octets = [
        octet if isinstance(octet, int) else ord(octet)
        for octet in packed
    ]

    first = octets[0]
    second = octets[1]

    is_ipv4_mapped = (
        all(b == 0 for b in octets[:10]) and
        octets[10] == 0xff and octets[11] == 0xff
    )
    if is_ipv4_mapped:
        ipv4_str = '.'.join(str(b) for b in octets[12:16])
        return _is_private_ipv4(ipv4_str)

    is_ipv4_compatible = all(b == 0 for b in octets[:12])
    if is_ipv4_compatible:
        ipv4_str = '.'.join(str(b) for b in octets[12:16])
        if _is_private_ipv4(ipv4_str):
            return True

    return (
        ip == '::1' or
        ip == '::' or
        (first == 0xfe and (second & 0xc0) == 0x80) or
        (first & 0xfe) == 0xfc
    )


def _is_private_host(hostname):
    """
    Check if a hostname is a private/internal address or metadata endpoint.
    """
    if not hostname:
        return True

    hostname = hostname.strip().lower().rstrip('.')
    if hostname in ('localhost', 'metadata', 'metadata.google.internal'):
        return True

    if _is_private_ipv4(hostname) or _is_private_ipv6(hostname):
        return True

    return False


def _validate_outbound_url(url, require_https=False):
    """
    Validate that a URL is safe for outbound requests.

    Raises UnsafeOutboundRequest if:
    - Scheme is not http or https
    - require_https=True and scheme is not https
    - URL has no host
    - URL contains userinfo
    - Host is private/internal
    - URL contains a fragment

    Returns the URL unchanged if valid.
    """
    parsed_url = urlsplit(url)
    scheme = parsed_url.scheme.lower()

    if scheme not in ('http', 'https'):
        raise UnsafeOutboundRequest('Unsupported outbound URL scheme')

    if require_https and scheme != 'https':
        raise UnsafeOutboundRequest(
            'Authenticated outbound requests require HTTPS'
        )

    if not parsed_url.netloc:
        raise UnsafeOutboundRequest('Outbound URL is missing a host')

    if _has_userinfo(parsed_url):
        raise UnsafeOutboundRequest('Outbound URL userinfo is not allowed')

    try:
        hostname = parsed_url.hostname
        parsed_url.port
    except Exception:
        raise UnsafeOutboundRequest('Outbound URL host is invalid')

    if _is_private_host(hostname):
        raise UnsafeOutboundRequest('Outbound URL host is not allowed')

    if parsed_url.fragment:
        raise UnsafeOutboundRequest('Outbound URL fragments are not allowed')

    return url


def _build_api_url(endpoint, api_uri):
    """
    Build a full API URL from an endpoint base and a relative URI.

    The endpoint must be an absolute URL. The api_uri must be a relative path
    with no scheme, host, userinfo, or fragment.
    """
    endpoint = (endpoint or '').strip()
    api_uri = (api_uri or '').strip()

    endpoint_parts = urlsplit(endpoint)
    if (
        endpoint_parts.scheme.lower() not in ('http', 'https') or
        not endpoint_parts.netloc or
        _has_userinfo(endpoint_parts) or
        endpoint_parts.fragment
    ):
        raise UnsafeOutboundRequest('Product endpoint is not allowed')

    api_parts = urlsplit(api_uri)
    if (
        api_parts.scheme or
        api_parts.netloc or
        _has_userinfo(api_parts) or
        api_parts.fragment
    ):
        raise UnsafeOutboundRequest('API URI must be a relative path')

    path = api_parts.path
    if not path.startswith('/'):
        path = '/%s' % path

    base_path = endpoint_parts.path.rstrip('/')
    full_path = '%s%s' % (base_path, path)

    return urlunsplit((
        endpoint_parts.scheme,
        endpoint_parts.netloc,
        full_path,
        api_parts.query,
        ''
    ))


def _validate_endpoint_hostname(endpoint, url):
    """
    Validate that a URL's hostname matches the expected endpoint.

    Endpoints with template variables like {region} are converted into a
    conservative hostname regex.
    """
    endpoint_hostname = urlsplit(endpoint).hostname
    url_hostname = urlsplit(url).hostname

    if not endpoint_hostname or not url_hostname:
        raise UnsafeOutboundRequest('Outbound URL host is invalid')

    endpoint_hostname = endpoint_hostname.lower()
    url_hostname = url_hostname.lower()

    if '{' not in endpoint_hostname:
        if url_hostname != endpoint_hostname:
            raise UnsafeOutboundRequest('Outbound URL host is not allowed')
        return

    pattern = re.escape(endpoint_hostname)
    pattern = re.sub(r'\\\{[^}]+\\\}', r'[a-z0-9-]+', pattern)
    if not re.match(r'^%s$' % pattern, url_hostname):
        raise UnsafeOutboundRequest('Outbound URL host is not allowed')


def sanitize_query_filter(filter_value):
    """
    Remove query/fragment/header-injection control characters from filters.
    """
    if not filter_value:
        return ''
    return re.sub(r'[\?#\r\n\x00]', '', filter_value)

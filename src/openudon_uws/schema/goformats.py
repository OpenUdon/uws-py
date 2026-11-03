"""Go ``net/url``, ``net/netip`` and santhosh-tekuri/jsonschema format checks, ported byte for byte.

Go works on the UTF-8 bytes of a string, so every function here does too. Component values are returned as
``str`` decoded with ``surrogateescape`` so that bytes produced by percent-decoding survive unchanged.
These follow Go's acceptance rules (Go 1.25), not RFC 3986.
"""

from __future__ import annotations

from dataclasses import dataclass

_ALNUM = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_HOST_EXTRA = frozenset(b"!$&'()*+,;=:[]<>\"")
_UNRESERVED_MARK = frozenset(b"-_.~")
_HEX = frozenset(b"0123456789abcdefABCDEF")
_USERINFO_OK = frozenset(b"-._:~!$&'()*+,;=%@") | _ALNUM


def _b(s: str) -> bytes:
    return s.encode("utf-8", errors="surrogateescape")


def _s(b: bytes) -> str:
    return b.decode("utf-8", errors="surrogateescape")


def _host_should_escape(c: int) -> bool:
    """``shouldEscape(c, encodeHost)`` (identical for ``encodeZone``)."""
    return not (c in _ALNUM or c in _HOST_EXTRA or c in _UNRESERVED_MARK)


class _URLError(ValueError):
    pass


def _unescape(s: bytes, mode: str) -> bytes:
    """Go ``unescape``; ``mode`` is ``host``, ``zone``, ``path``, ``fragment`` or ``user``."""
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == 0x25:  # %
            if i + 2 >= n or s[i + 1] not in _HEX or s[i + 2] not in _HEX:
                raise _URLError("invalid URL escape")
            v = int(s[i + 1 : i + 3], 16)
            if mode == "host" and v < 0x80 and s[i : i + 3] != b"%25":
                raise _URLError("invalid URL escape in host")
            if mode == "zone" and s[i : i + 3] != b"%25" and v != 0x20 and _host_should_escape(v):
                raise _URLError("invalid URL escape in zone")
            i += 3
            continue
        if mode in ("host", "zone") and c < 0x80 and _host_should_escape(c):
            raise _URLError("invalid character in host name")
        i += 1
    out = bytearray()
    i = 0
    while i < n:
        if s[i] == 0x25:
            out.append(int(s[i + 1 : i + 3], 16))
            i += 3
        else:
            out.append(s[i])
            i += 1
    return bytes(out)


def _valid_optional_port(port: bytes) -> bool:
    if port == b"":
        return True
    if port[:1] != b":":
        return False
    return all(0x30 <= b <= 0x39 for b in port[1:])


# --- net/netip ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IPAddr:
    bytes16: bytes
    zone: bytes
    is4: bool


def _ipv4_fields(s: bytes) -> bytes | None:
    """``parseIPv4Fields``."""
    fields: list[int] = []
    val = 0
    dig_len = 0
    for i, c in enumerate(s):
        if 0x30 <= c <= 0x39:
            if dig_len == 1 and val == 0:
                return None
            val = val * 10 + c - 0x30
            dig_len += 1
            if val > 255:
                return None
        elif c == 0x2E:
            if i == 0 or i == len(s) - 1 or s[i - 1] == 0x2E:
                return None
            if len(fields) == 3:
                return None
            fields.append(val)
            val = 0
            dig_len = 0
        else:
            return None
    if len(fields) < 3:
        return None
    fields.append(val)
    return bytes(fields)


def _parse_ipv6(whole: bytes) -> IPAddr | None:
    s = whole
    zone = b""
    pct = s.find(b"%")
    if pct != -1:
        s, zone = s[:pct], s[pct + 1 :]
        if zone == b"":
            return None
    ip = bytearray(16)
    ellipsis = -1
    if len(s) >= 2 and s[0] == 0x3A and s[1] == 0x3A:
        ellipsis = 0
        s = s[2:]
        if len(s) == 0:
            return IPAddr(bytes(16), zone, False)
    i = 0
    while i < 16:
        off = 0
        acc = 0
        while off < len(s):
            c = s[off]
            if c in _HEX:
                acc = (acc << 4) + int(chr(c), 16)
            else:
                break
            if off > 3 or acc > 0xFFFF:
                return None
            off += 1
        if off == 0:
            return None
        if off < len(s) and s[off] == 0x2E:
            if ellipsis < 0 and i != 12:
                return None
            if i + 4 > 16:
                return None
            v4 = _ipv4_fields(s)
            if v4 is None:
                return None
            ip[i : i + 4] = v4
            s = b""
            i += 4
            break
        ip[i] = (acc >> 8) & 0xFF
        ip[i + 1] = acc & 0xFF
        i += 2
        s = s[off:]
        if len(s) == 0:
            break
        if s[0] != 0x3A or len(s) == 1:
            return None
        s = s[1:]
        if s[0] == 0x3A:
            if ellipsis >= 0:
                return None
            ellipsis = i
            s = s[1:]
            if len(s) == 0:
                break
    if len(s) != 0:
        return None
    if i < 16:
        if ellipsis < 0:
            return None
        n = 16 - i
        for j in range(i - 1, ellipsis - 1, -1):
            ip[j + n] = ip[j]
        ip[ellipsis : ellipsis + n] = bytes(n)
    elif ellipsis >= 0:
        return None
    return IPAddr(bytes(ip), zone, False)


def parse_addr(text: str | bytes) -> IPAddr | None:
    """``netip.ParseAddr``; ``None`` where Go returns an error."""
    s = text if isinstance(text, bytes) else _b(text)
    for c in s:
        if c == 0x2E:
            v4 = _ipv4_fields(s)
            return None if v4 is None else IPAddr(b"\x00" * 10 + b"\xff\xff" + v4, b"", True)
        if c == 0x3A:
            return _parse_ipv6(s)
        if c == 0x25:
            return None
    return None


def is_loopback_ip(text: str) -> bool:
    """``net.ParseIP(text).IsLoopback()``."""
    addr = parse_addr(text)
    if addr is None or addr.zone:
        return False
    b = addr.bytes16
    if b[:10] == bytes(10) and b[10:12] == b"\xff\xff":
        return b[12] == 127
    return b == bytes(15) + b"\x01"


# --- net/url --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GoURL:
    """Components of a URL as Go's ``url.Parse`` produces them (``host`` is unescaped, as ``u.Host``)."""

    scheme: str
    opaque: str
    user: str | None
    host: str
    path: str
    raw_query: str
    force_query: bool
    fragment: str

    @property
    def is_abs(self) -> bool:
        return self.scheme != ""

    def hostname(self) -> str:
        return split_host_port(self.host)[0]

    def port(self) -> str:
        return split_host_port(self.host)[1]


def split_host_port(host_port: str) -> tuple[str, str]:
    """Go ``splitHostPort`` (``u.Hostname()`` / ``u.Port()``)."""
    host = _b(host_port)
    port = b""
    colon = host.rfind(b":")
    if colon != -1 and _valid_optional_port(host[colon:]):
        host, port = host[:colon], host[colon + 1 :]
    if host.startswith(b"[") and host.endswith(b"]"):
        host = host[1:-1]
    return _s(host), _s(port)


def _parse_host(host: bytes) -> bytes:
    open_idx = host.rfind(b"[")
    if open_idx != -1:
        close_idx = host.rfind(b"]")
        if close_idx < 0:
            raise _URLError("missing ']' in host")
        colon_port = host[close_idx + 1 :]
        if not _valid_optional_port(colon_port):
            raise _URLError("invalid port after host")
        unescaped_port = _unescape(colon_port, "host")
        name = host[open_idx + 1 : close_idx]
        zone_idx = name.find(b"%25")
        if zone_idx >= 0:
            unescaped = _unescape(name[:zone_idx], "host") + _unescape(name[zone_idx:], "zone")
        else:
            unescaped = _unescape(name, "host")
        addr = parse_addr(unescaped)
        if addr is None:
            raise _URLError("invalid host")
        if addr.is4:
            raise _URLError("invalid IP-literal")
        return b"[" + unescaped + b"]" + unescaped_port
    colon = host.rfind(b":")
    if colon != -1 and not _valid_optional_port(host[colon:]):
        raise _URLError("invalid port after host")
    return _unescape(host, "host")


def _valid_userinfo(s: bytes) -> bool:
    return all(c in _USERINFO_OK for c in s)


def _parse_authority(authority: bytes) -> tuple[bytes | None, bytes]:
    at = authority.rfind(b"@")
    host = _parse_host(authority if at < 0 else authority[at + 1 :])
    if at < 0:
        return None, host
    userinfo = authority[:at]
    if not _valid_userinfo(userinfo):
        raise _URLError("invalid userinfo")
    if b":" not in userinfo:
        _unescape(userinfo, "user")
    else:
        username, _, password = userinfo.partition(b":")
        _unescape(username, "user")
        _unescape(password, "user")
    return userinfo, host


def _get_scheme(raw: bytes) -> tuple[bytes, bytes]:
    for i, c in enumerate(raw):
        if (0x61 <= c <= 0x7A) or (0x41 <= c <= 0x5A):
            continue
        if (0x30 <= c <= 0x39) or c in b"+-.":
            if i == 0:
                return b"", raw
            continue
        if c == 0x3A:
            if i == 0:
                raise _URLError("missing protocol scheme")
            return raw[:i], raw[i + 1 :]
        return b"", raw
    return b"", raw


def parse_url(raw_url: str) -> GoURL | None:
    """Go ``url.Parse``; ``None`` where Go returns an error."""
    raw = _b(raw_url)
    rest_all, has_frag, frag = raw.partition(b"#")
    try:
        u = _parse(rest_all)
        fragment = _s(_unescape(frag, "fragment")) if has_frag and frag else ""
    except _URLError:
        return None
    return GoURL(u.scheme, u.opaque, u.user, u.host, u.path, u.raw_query, u.force_query, fragment)


def _parse(raw: bytes) -> GoURL:
    if any(b < 0x20 or b == 0x7F for b in raw):
        raise _URLError("invalid control character in URL")
    if raw == b"*":
        return GoURL("", "", None, "", "*", "", False, "")
    scheme_b, rest = _get_scheme(raw)
    scheme = _s(scheme_b).lower()
    force_query = False
    raw_query = b""
    if rest.endswith(b"?") and rest.count(b"?") == 1:
        force_query = True
        rest = rest[:-1]
    else:
        rest, _, raw_query = rest.partition(b"?")
    if not rest.startswith(b"/"):
        if scheme != "":
            return GoURL(scheme, _s(rest), None, "", "", _s(raw_query), force_query, "")
        segment = rest.partition(b"/")[0]
        if b":" in segment:
            raise _URLError("first path segment in URL cannot contain colon")
    user: bytes | None = None
    host = b""
    if (scheme != "" or not rest.startswith(b"///")) and rest.startswith(b"//"):
        authority, rest = rest[2:], b""
        slash = authority.find(b"/")
        if slash >= 0:
            authority, rest = authority[:slash], authority[slash:]
        user, host = _parse_authority(authority)
    path = _unescape(rest, "path")
    return GoURL(scheme, "", None if user is None else _s(user), _s(host), _s(path), _s(raw_query), force_query, "")


# --- santhosh-tekuri/jsonschema/v6 formats ------------------------------------------------------------------------


def _schema_parse_url(value: str) -> GoURL | None:
    """santhosh-tekuri ``parseURL``: ``url.Parse`` plus bracketed, valid, zone-free IPv6 hosts."""
    u = parse_url(value)
    if u is None:
        return None
    name = u.hostname()
    if ":" in name:
        if "[" not in u.host or "]" not in u.host:
            return None
        if not _valid_ipv6_format(name):
            return None
    return u


def _valid_ipv6_format(value: str) -> bool:
    """santhosh-tekuri ``validateIPV6``."""
    if ":" not in value:
        return False
    addr = parse_addr(value)
    return addr is not None and addr.zone == b""


def go_uri(value: str) -> bool:
    u = _schema_parse_url(value)
    return u is not None and u.is_abs


def go_uri_reference(value: str) -> bool:
    return "\\" not in value and _schema_parse_url(value) is not None


def _atoi(tok: bytes) -> int | None:
    """``strconv.Atoi`` (optional sign, ASCII digits)."""
    body = tok[1:] if tok[:1] in (b"+", b"-") else tok
    if not body or not all(0x30 <= c <= 0x39 for c in body):
        return None
    return int(tok)


_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _go_date(s: bytes) -> bool:
    """``time.Parse("2006-01-02", s)``."""
    if len(s) != 10 or s[4] != 0x2D or s[7] != 0x2D:
        return False
    parts = (s[0:4], s[5:7], s[8:10])
    if not all(all(0x30 <= c <= 0x39 for c in p) for p in parts):
        return False
    year, month, day = (int(p) for p in parts)
    if not 1 <= month <= 12:
        return False
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = 29 if month == 2 and leap else _DAYS[month - 1]
    return 1 <= day <= days


def _go_time(s: bytes) -> bool:
    """santhosh-tekuri ``validateTime`` (RFC 3339 full-time; leap second only at 23:59 UTC)."""
    if len(s) < 9 or s[2] != 0x3A or s[5] != 0x3A:
        return False
    hms: list[int] = []
    for tok in s[:8].split(b":", 2):
        n = _atoi(tok)
        if n is None or n < 0:
            return False
        hms.append(n)
    if len(hms) != 3:
        return False
    h, m, sec = hms
    if h > 23 or m > 59 or sec > 60:
        return False
    rest = s[8:]
    if rest.startswith(b"."):
        rem = rest[1:]
        digits = 0
        while digits < len(rem) and 0x30 <= rem[digits] <= 0x39:
            digits += 1
        if digits == 0:
            return False
        rest = rem[digits:]
    if rest not in (b"z", b"Z"):
        if len(rest) != 6:
            return False
        if rest[0] == 0x2B:
            sign = -1
        elif rest[0] == 0x2D:
            sign = 1
        else:
            return False
        off = rest[1:]
        if off[2] != 0x3A:
            return False
        zhm: list[int] = []
        for tok in off.split(b":", 1):
            n = _atoi(tok)
            if n is None or n < 0:
                return False
            zhm.append(n)
        zh, zm = zhm
        if zh > 23 or zm > 59:
            return False
        hm = (h * 60 + m) + sign * (zh * 60 + zm)
        if hm < 0:
            hm += 24 * 60
        h, m = hm // 60, hm % 60
    return not (sec >= 60 and (h != 23 or m != 59))


def go_date_time(value: str) -> bool:
    """santhosh-tekuri ``validateDateTime``."""
    s = _b(value)
    if len(s) < 20:
        return False
    if s[10] not in (0x74, 0x54):
        return False
    return _go_date(s[:10]) and _go_time(s[11:])

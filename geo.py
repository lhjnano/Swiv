# swiv country filter: refuse connections from outside the configured countries.
# Range data: the public RIR delegation files, cached under ~/.cache/swiv/geo.
import bisect
import ipaddress
import os
import time
import urllib.request

# the five regional internet registries, one delegation file each
RIRS = {
    "apnic": "https://ftp.apnic.net/stats/apnic/delegated-apnic-latest",
    "ripe": "https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-latest",
    "arin": "https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest",
    "lacnic": "https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest",
    "afrinic": "https://ftp.afrinic.net/pub/stats/afrinic/delegated-afrinic-extended-latest",
}
REFRESH_SEC = 7 * 86400
FETCH_TIMEOUT = 30

_v4, _v4s = [], []  # merged (start, end) ranges and their starts, for bisect
_v6, _v6s = [], []


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "swiv", "geo")


def _fetch(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "swiv/geo"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:  # nosec B310 - fixed RIR urls
        data = r.read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _parse(path, countries):
    """(start, end) integer ranges of these countries from one delegation file."""
    out = {4: [], 6: []}
    try:
        f = open(path)
    except OSError:
        return out
    with f:
        for line in f:
            p = line.split("|")
            if len(p) < 5 or p[1] not in countries or p[2] not in ("ipv4", "ipv6"):
                continue
            try:
                if p[2] == "ipv4":
                    s = int(ipaddress.IPv4Address(p[3]))
                    out[4].append((s, s + int(p[4]) - 1))
                else:
                    s = int(ipaddress.IPv6Address(p[3]))
                    out[6].append((s, s + 2 ** (128 - int(p[4])) - 1))
            except ValueError:
                continue
    return out


def _merge(ranges):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def load(countries, cache_dir=None, refresh_sec=REFRESH_SEC, rirs=None):
    """Build the tables; returns the RIRs whose data could not be refreshed."""
    global _v4, _v4s, _v6, _v6s
    want = {c.upper() for c in countries}
    cache = cache_dir or _cache_dir()
    fresh = time.time() - refresh_sec
    rng, missing = {4: [], 6: []}, []
    for name, url in (rirs or RIRS).items():
        path = os.path.join(cache, name)
        if not os.path.exists(path) or os.path.getmtime(path) < fresh:
            try:
                _fetch(url, path)
            except OSError as e:
                missing.append(f"{name} ({e})")
        for v, rs in _parse(path, want).items():
            rng[v] += rs
    _v4 = _merge(rng[4])
    _v4s = [s for s, _ in _v4]
    _v6 = _merge(rng[6])
    _v6s = [s for s, _ in _v6]
    return missing


def count():
    return len(_v4) + len(_v6)


def allowed(ip, countries):
    """True when this client may connect: no filter, local network, listed country."""
    if not countries:
        return True
    a = ipaddress.ip_address(ip)
    if a.is_private or a.is_loopback or a.is_link_local:
        return True
    n = int(a)
    nets, starts = (_v4, _v4s) if a.version == 4 else (_v6, _v6s)
    i = bisect.bisect_right(starts, n) - 1
    return i >= 0 and nets[i][0] <= n <= nets[i][1]

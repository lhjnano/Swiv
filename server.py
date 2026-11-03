# swiv: phone view of desktop terminals (AT-SPI) and CDP browser pages as swipe cards.
# endpoints: /login, / (page.html), /cards, /open, /leave, /term, /shot, /input, /new, /close
# setup: server.py --set-password  (writes port + scrypt hash to the config file)
import atexit
import getpass
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import signal
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import auth
import cards
import config
import files
import geo
import screen
import theme

# request limits and clamps for phone-supplied values
MAX_BODY = 8192          # bytes of a POST body
MAX_INPUT = 2000         # characters typed into a terminal per request
LOGIN_FAIL_DELAY = 1     # seconds held after a wrong password
TERM_COLS, TERM_ROWS = (20, 300), (5, 200)
PAGE_W, PAGE_H, PAGE_DPR = (200, 2000), (200, 3000), (1, 3)
WALLPAPER_CACHE, FONT_CACHE = "private, max-age=3600", "private, max-age=86400"
PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
SESSIONS = {}  # token -> expiry epoch; in memory, so a restart logs everyone out
FTOKENS = {}  # file token -> expiry; sandboxed documents cannot send the cookie
SESSION_FTOK = {}  # session token -> its file token
# \Z, not $: $ would also accept a trailing newline
CARD_RE = re.compile(r"^(t0x[0-9a-fA-F]+\.\d+|t\d+\.\d+|p\d+\.[A-Fa-f0-9]{16,64}|s\d{1,2})\Z")


def _clamp(v, lo_hi):
    return min(max(v, lo_hi[0]), lo_hi[1])


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def set_password():
    pw = getpass.getpass("new swiv password: ")
    if len(pw) < 8 or pw != getpass.getpass("again: "):
        sys.exit("password must be 8+ chars and match")
    cfg = config.read_file()
    salt = secrets.token_bytes(16)
    cfg.update(auth="password", salt=salt.hex(), hash=hash_password(pw, salt))
    config.save(cfg)
    print(f"saved to {config.PATH}")


def check_password(cfg, pw):
    return hmac.compare_digest(hash_password(pw, bytes.fromhex(cfg["salt"])), cfg["hash"])


def login_user():
    # PAM run as a normal user can only verify that same user (unix_chkpwd), so the
    # desktop owner is the only account that can log in
    return getpass.getuser()


def check_login(cfg, pw):
    if cfg["auth"] == "pam":
        import pam
        return pam.pam().authenticate(login_user(), pw, service=cfg["pam_service"])
    return check_password(cfg, pw)


LOGIN = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>swiv</title>
<style>body{margin:0;min-height:100dvh;display:grid;place-items:center;background:#111;
font:16px system-ui,sans-serif;color:#eee}form{display:grid;gap:12px;width:min(320px,90vw)}
input,button{font:inherit;padding:12px;border-radius:8px;border:1px solid #444}
input{background:#222;color:#eee}button{background:#3b6;color:#fff;border:0}
p{color:#f77;margin:0}</style>
<form method=post action=/login><label for=pw>%s</label>
<input id=pw name=pw type=password autocomplete=current-password autofocus required>
%s<button>login</button></form>"""


LIMITER = auth.Limiter()  # main() rebuilds it from the config


def log(msg):
    # audit trail: stdout is the journal under the service, or a redirect when run by hand
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


class H(BaseHTTPRequestHandler):
    cfg = None

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body, headers=(), cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _who(self):
        return html.escape(f"swiv: {login_user()}" if self.cfg["auth"] == "pam" else "swiv")

    def _login_page(self, code, msg):
        err = f"<p>{html.escape(msg)}</p>" if msg else ""
        self._send(code, "text/html", (LOGIN % (self._who(), err)).encode())

    def _locked(self, wait):
        minutes = (wait + 59) // 60
        msg = f"too many failed attempts, try again in {minutes} min"
        self._send(429, "text/html", (LOGIN % (self._who(), f"<p>{msg}</p>")).encode(),
                   [("Retry-After", str(wait))])

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj).encode())

    def _redirect(self, to, headers=()):
        self._send(303, "text/plain", b"", [("Location", to), *headers])

    def _geo_ok(self):
        # country filter, off by default; local networks always pass (geo.py)
        return geo.allowed(self.client_address[0], self.cfg.get("allowed_countries") or [])

    def _authed(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "swiv" and SESSIONS.get(v, 0) > time.time():
                return v
        return None

    def _body(self):
        n = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
        return self.rfile.read(n).decode(errors="replace")

    def _card(self, cid):
        if not isinstance(cid, str) or not CARD_RE.match(cid):
            self._send(400, "text/plain", b"bad card id")
            return None
        return cid

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._geo_ok():
            return self._send(403, "text/plain", b"blocked country")
        if path == "/login":
            ip = self.client_address[0]
            pw = urllib.parse.parse_qs(self._body()).get("pw", [""])[0]
            wait = LIMITER.blocked(ip)
            if wait:
                if LIMITER.note_blocked(ip):  # one line per lockout period, floods stay quiet
                    log(f"login blocked from {ip} (retry in {wait}s)")
                return self._locked(wait)
            if not LIMITER.attempt.acquire(blocking=False):
                return self._login_page(429, "another login attempt is in progress, try again")
            try:
                ok = check_login(self.cfg, pw)
                if not ok:
                    left = LIMITER.failed(ip)
                    time.sleep(LOGIN_FAIL_DELAY)  # slow down guessing, while still holding the attempt lock
            finally:
                LIMITER.attempt.release()
            if not ok:
                wait = LIMITER.blocked(ip)
                log(f"login failed from {ip}" + (f", lockout {wait}s" if wait else ""))
                if wait:
                    return self._locked(wait)
                return self._login_page(401, f"wrong password ({left} tries left)")
            LIMITER.succeeded(ip)
            log(f"login ok from {ip}")
            token = secrets.token_urlsafe(32)
            ttl = int(self.cfg["session_days"]) * 86400
            SESSIONS[token] = time.time() + ttl
            ftok = secrets.token_urlsafe(24)
            FTOKENS[ftok], SESSION_FTOK[token] = SESSIONS[token], ftok
            cookie = f"swiv={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={ttl}"
            return self._redirect("/", [("Set-Cookie", cookie)])
        if not self._authed():
            return self._send(401, "text/plain", b"login required")
        try:
            body = json.loads(self._body() or "{}")
        except ValueError:
            return self._send(400, "text/plain", b"bad json")
        if path == "/new":
            if body.get("kind") == "term":
                if not cards.term_new():
                    return self._send(503, "text/plain", b"terminal cards need gnome-terminal and AT-SPI")
                return self._json({"ok": True})
            url = str(body.get("url") or "about:blank").strip()
            if "://" not in url and url != "about:blank":
                url = "https://" + url
            if url != "about:blank" and urllib.parse.urlparse(url).scheme not in ("http", "https"):
                return self._send(400, "text/plain", b"only http/https urls")
            ok = cards.page_new(int(self.cfg["cdp_ports"][0]), os.path.expanduser(self.cfg["chrome_profile"]),
                                url, self.cfg["chrome_bin"])
            if not ok:
                return self._send(503, "text/plain", b"no chrome/chromium found; set chrome_bin in config")
            return self._json({"ok": True})
        cid = self._card(body.get("id"))
        if cid is None:
            return
        try:
            if path == "/open":
                if cid.startswith("s"):
                    ok = screen.open_(cid, round(_clamp(int(body["width"]), PAGE_W) *
                                                 _clamp(float(body.get("dpr", 1)), PAGE_DPR)))
                elif cid.startswith("t"):
                    ok = cards.term_open(cid, _clamp(int(body["cols"]), TERM_COLS),
                                         _clamp(int(body["rows"]), TERM_ROWS))
                else:
                    ok = cards.page_open(cid, _clamp(int(body["width"]), PAGE_W),
                                         _clamp(int(body["height"]), PAGE_H),
                                         _clamp(float(body.get("dpr", 1)), PAGE_DPR))
                return self._json({"ok": ok}, 200 if ok else 404)
            if path == "/leave":
                cards.leave(cid)
                return self._json({"ok": True})
            if path == "/close":
                if cid.startswith("s"):
                    return self._send(400, "text/plain", b"a screen cannot be closed")
                ok = cards.term_close(cid) if cid.startswith("t") else cards.page_close(cid)
                return self._json({"ok": ok}, 200 if ok else 404)
            if path == "/input":
                if cid.startswith("s"):
                    ok = screen.input_(cid, body)
                elif cid.startswith("t"):
                    ok = cards.term_input(cid, str(body.get("text", ""))[:MAX_INPUT], str(body.get("key", "")))
                else:
                    ok = cards.page_input(cid, body)
                return self._json({"ok": ok}, 200 if ok else 404)
        except (KeyError, ValueError, TypeError, StopIteration, OSError) as e:
            return self._send(400, "text/plain", str(e).encode())
        return self._send(404, "text/plain", b"not found")

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if not self._geo_ok():
            return self._send(403, "text/plain", b"blocked country")
        if path == "/login":
            wait = LIMITER.blocked(self.client_address[0])
            return self._locked(wait) if wait else self._login_page(200, "")
        if path.startswith("/f/"):
            ftok, _, rel = path[3:].partition("/")
            if FTOKENS.get(ftok, 0) <= time.time():
                return self._send(401, "text/plain", b"login required")
            raw = "raw" in urllib.parse.parse_qs(query)
            if not files.serve(self, files.root_of(self.cfg), urllib.parse.unquote(rel), raw):
                return self._send(404, "text/plain", b"not found")
            return
        token = self._authed()
        if not token:
            if path == "/":
                return self._redirect("/login")
            return self._send(401, "text/plain", b"login required")
        if path == "/":
            with open(PAGE_FILE, "rb") as f:
                return self._send(200, "text/html", f.read())
        if path == "/cards":
            return self._json([{"kind": "files", "id": "files", "title": "files"}] + cards.list_cards(self.cfg))
        if path == "/theme":
            return self._json(theme.summary())
        if path == "/wallpaper":
            jpg = theme.wallpaper_jpeg(int(self.cfg["wallpaper_width"]))
            return self._send(404, "text/plain", b"no wallpaper") if jpg is None else \
                self._send(200, "image/jpeg", jpg, cache=WALLPAPER_CACHE)
        if path == "/font":
            fp = theme.font_path()
            if not fp:
                return self._send(404, "text/plain", b"no font")
            with open(fp, "rb") as f:
                return self._send(200, "font/ttf" if fp.endswith(".ttf") else "font/otf", f.read(),
                                  cache=FONT_CACHE)
        if path == "/files":
            rel = urllib.parse.parse_qs(query).get("path", [""])[0]
            data = files.listing(files.root_of(self.cfg), rel)
            if data is None:
                return self._send(404, "text/plain", b"no such folder")
            return self._json({**data, "ftok": SESSION_FTOK.get(token, "")})
        cid = self._card(urllib.parse.parse_qs(query).get("id", [""])[0])
        if cid is None:
            return
        if path == "/term":
            segs = cards.term_text(cid)
            return self._send(404, "text/plain", b"gone") if segs is None else self._json(segs)
        if path == "/shot":
            try:
                jpg = screen.shot(cid) if cid.startswith("s") else cards.page_shot(cid)
            except (OSError, ValueError, websocket_errors()) as e:
                return self._send(502, "text/plain", str(e).encode())
            return self._send(404, "text/plain", b"not open") if jpg is None else \
                self._send(200, "image/jpeg", jpg)
        return self._send(404, "text/plain", b"not found")


def websocket_errors():
    return cards.websocket.WebSocketException


def main():
    if "--set-password" in sys.argv:
        set_password()
        return
    try:
        H.cfg = config.load()
    except ValueError as e:
        sys.exit(f"bad config {config.PATH}: {e}")
    if H.cfg["auth"] == "pam":
        try:
            import pam  # noqa: F401
        except ImportError:
            sys.exit("auth=pam needs the python-pam module (make venv)")
    elif H.cfg["auth"] == "password":
        if not (H.cfg.get("hash") and H.cfg.get("salt")):
            sys.exit(f"auth=password but no password set; run: {sys.argv[0]} --set-password")
    else:
        sys.exit(f"unknown auth {H.cfg['auth']!r}; use pam or password")
    if H.cfg.get("allowed_countries"):
        codes = [str(c).upper() for c in H.cfg["allowed_countries"]]
        missing = geo.load(codes)
        if missing:
            log(f"warning: country data missing, non-local refused: {'; '.join(missing)}")
        log(f"country filter on: {', '.join(codes)} ({geo.count()} ranges)")
    os.environ["DISPLAY"] = H.cfg["display"]  # new windows go to the monitor session
    cards.IDLE_RESTORE = int(H.cfg["idle_restore_sec"])
    atexit.register(cards.restore_all)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # run atexit restore on kill
    global LIMITER
    LIMITER = auth.Limiter(int(H.cfg["login_max_failures"]), int(H.cfg["login_global_max_failures"]),
                           int(H.cfg["login_window_min"]) * 60, int(H.cfg["login_lockout_min"]) * 60)
    if H.cfg["auth"] == "pam":
        mods = auth.pam_lockout_modules(H.cfg["pam_service"], H.cfg["pam_dir"])
        if mods:
            print(f"warning: PAM service {H.cfg['pam_service']!r} uses {', '.join(mods)}; keep "
                  "login_global_max_failures below its deny count or the account can lock", flush=True)
    threading.Thread(target=cards.reaper, daemon=True).start()
    srv = ThreadingHTTPServer((H.cfg["bind"], int(H.cfg["port"])), H)
    print(f"swiv listening on {H.cfg['bind']}:{H.cfg['port']} (config {config.PATH})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

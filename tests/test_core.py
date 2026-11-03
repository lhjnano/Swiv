# Self tests for the security-relevant parts: auth, throttling, file sandboxing,
# config and card ids. Run with `make test`.
import http.client
import json
import os
import secrets
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SWIV_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

import auth  # noqa: E402
import config  # noqa: E402
import files  # noqa: E402
import server  # noqa: E402


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


class Config(unittest.TestCase):
    def test_defaults_merge_with_file(self):
        write(config.PATH, json.dumps({"port": 7000, "files_root": "/srv"}))
        try:
            cfg = config.load()
        finally:
            os.remove(config.PATH)
        self.assertEqual(cfg["port"], 7000)
        self.assertEqual(cfg["files_root"], "/srv")
        self.assertEqual(cfg["cdp_ports"], config.DEFAULTS["cdp_ports"])

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load(), config.DEFAULTS)


class Password(unittest.TestCase):
    def test_check(self):
        salt = secrets.token_bytes(16)
        cfg = {"auth": "password", "salt": salt.hex(), "hash": server.hash_password("correct horse", salt)}
        self.assertTrue(server.check_login(cfg, "correct horse"))
        self.assertFalse(server.check_login(cfg, "wrong"))


class Pam(unittest.TestCase):
    def test_wrong_system_password_rejected(self):
        cfg = {**config.DEFAULTS, "auth": "pam"}
        self.assertFalse(server.check_login(cfg, "not-the-real-password-" + secrets.token_hex(4)))

    def test_lockout_module_detection(self):
        d = tempfile.mkdtemp()
        write(os.path.join(d, "svc"), "@include common-auth\naccount include extra\n")
        write(os.path.join(d, "common-auth"), "auth required pam_unix.so\n")
        write(os.path.join(d, "extra"), "auth required pam_faillock.so preauth\n")
        self.assertEqual(auth.pam_lockout_modules("svc", d), ["pam_faillock"])
        self.assertEqual(auth.pam_lockout_modules("common-auth", d), [])


class CloseLabels(unittest.TestCase):
    def test_english_fallback(self):
        import cards
        labels = cards.close_labels()
        self.assertIn("close terminal", labels)
        self.assertTrue(all(lb == lb.lower() and "_" not in lb for lb in labels))


class Throttle(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.lim = auth.Limiter(per_ip=3, global_max=5, window_sec=60, lockout_sec=120,
                                clock=lambda: self.now[0])

    def test_ip_locks_after_max_then_unlocks(self):
        self.assertEqual(self.lim.failed("a"), 2)
        self.assertEqual(self.lim.failed("a"), 1)
        self.assertEqual(self.lim.failed("a"), 0)
        self.assertEqual(self.lim.blocked("a"), 120)
        self.assertEqual(self.lim.blocked("b"), 0)
        self.now[0] += 121
        self.assertEqual(self.lim.blocked("a"), 0)

    def test_success_resets(self):
        self.lim.failed("a")
        self.lim.failed("a")
        self.lim.succeeded("a")
        self.assertEqual(self.lim.failed("a"), 2)

    def test_old_failures_expire(self):
        self.lim.failed("a")
        self.lim.failed("a")
        self.now[0] += 61
        self.assertEqual(self.lim.failed("a"), 2)

    def test_global_lock_across_ips(self):
        for ip in ("a", "b", "c", "d", "e"):
            self.lim.failed(ip)
        self.assertGreater(self.lim.blocked("fresh-ip"), 0)


class Resolve(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        os.makedirs(os.path.join(self.root, "docs"))
        write(os.path.join(self.root, "docs", "a.md"), "# hi")
        os.symlink("/etc", os.path.join(self.root, "escape"))

    def test_inside(self):
        self.assertEqual(files.resolve(self.root, "docs/a.md"), os.path.join(self.root, "docs", "a.md"))

    def test_dotdot_blocked(self):
        self.assertIsNone(files.resolve(self.root, "../../etc/passwd"))
        self.assertIsNone(files.resolve(self.root, "docs/../../etc"))

    def test_symlink_out_blocked(self):
        self.assertIsNone(files.resolve(self.root, "escape/passwd"))

    def test_listing_hides_dotfiles(self):
        write(os.path.join(self.root, ".secret"), "x")
        names = [e["name"] for e in files.listing(self.root, "")["entries"]]
        self.assertIn("docs", names)
        self.assertNotIn(".secret", names)


class ScreenPoint(unittest.TestCase):
    def test_fraction_maps_into_monitor(self):
        import screen
        mon = {"left": 100, "top": 50, "width": 1920, "height": 1080}
        self.assertEqual(screen._point(mon, {"fx": 0, "fy": 0}), (100, 50))
        self.assertEqual(screen._point(mon, {"fx": 1, "fy": 1}), (2019, 1129))
        self.assertEqual(screen._point(mon, {"fx": 5, "fy": -1}), (2019, 50))  # clamped
        with self.assertRaises(ValueError):
            screen._point(mon, {"fx": "nan", "fy": 0})


class _Frame:  # stand-in for an Atspi frame
    def __init__(self, name, geo):
        self._n, self._g = name, geo

    def get_name(self):
        return self._n

    def get_extents(self, coord):
        e = type("E", (), dict(zip("x y width height".split(), self._g)))()
        return e


class WindowMatch(unittest.TestCase):
    def test_stacked_windows_resolved_by_title(self):
        import cards
        wins = {"111": ("a", (1, 2, 3, 4)), "222": ("b", (1, 2, 3, 4))}  # same spot
        used = set()
        self.assertEqual(cards._xid_of(_Frame("a", (1, 2, 3, 4)), wins, used), "111")
        used.add("111")
        self.assertEqual(cards._xid_of(_Frame("b", (1, 2, 3, 4)), wins, used), "222")

    def test_identical_windows_share_the_first(self):
        import cards
        wins = {"111": ("a", (1, 2, 3, 4)), "222": ("a", (1, 2, 3, 4))}  # twins
        used = set()
        self.assertEqual(cards._xid_of(_Frame("a", (1, 2, 3, 4)), wins, used), "111")


class CardIds(unittest.TestCase):
    def test_accept(self):
        for cid in ("t58720266.0", "t0x3800027f.2", "p9222." + "A" * 32, "s1"):
            self.assertTrue(server.CARD_RE.match(cid), cid)

    def test_reject(self):
        for cid in ("t1;rm -rf", "p9222./etc", "x1.0", "t1.0\n", "p9222.zz", "s123", "s1\n"):
            self.assertIsNone(server.CARD_RE.match(cid), cid)


class Geo(unittest.TestCase):
    DATA = ("apnic|KR|ipv4|1.2.3.0|256|20240101|allocated\n"
            "apnic|JP|ipv4|5.6.7.0|256|20240101|allocated\n"
            "apnic|KR|ipv6|2001:db8::|32|20240101|allocated\n"
            "apnic|KR|ipv4|9.9.9.0|nonsense|x\n")

    def load(self, rirs, with_data=True):
        import geo
        d = tempfile.mkdtemp()
        if with_data:
            write(os.path.join(d, "test"), self.DATA)
        return geo.load(["kr"], d, refresh_sec=10**9, rirs=rirs)

    def test_country_filter(self):
        import geo
        self.assertEqual(self.load({"test": "file:///unused"}), [])
        self.assertTrue(geo.allowed("1.2.3.4", ["KR"]))
        self.assertFalse(geo.allowed("5.6.7.4", ["KR"]))  # JP range
        self.assertTrue(geo.allowed("192.168.1.2", ["KR"]))  # local network
        self.assertTrue(geo.allowed("2001:db8::9", ["KR"]))
        self.assertFalse(geo.allowed("2001:db9::9", ["KR"]))
        self.assertTrue(geo.allowed("8.8.8.8", []))  # filter off

    def test_missing_data_refuses_public(self):
        import geo
        self.assertEqual(len(self.load({"test": "file:///nonexistent"}, with_data=False)), 1)
        self.assertFalse(geo.allowed("1.2.3.4", ["KR"]))
        self.assertTrue(geo.allowed("10.0.0.1", ["KR"]))


class BlockedAuditLine(unittest.TestCase):
    def test_one_line_per_lockout_period(self):
        t = [1000.0]
        lim = auth.Limiter(per_ip=2, window_sec=900, lockout_sec=600, clock=lambda: t[0])
        lim.failed("1.2.3.4")
        lim.failed("1.2.3.4")
        self.assertGreater(lim.blocked("1.2.3.4"), 0)
        self.assertTrue(lim.note_blocked("1.2.3.4"))   # first attempt: logged
        t[0] += 60
        self.assertFalse(lim.note_blocked("1.2.3.4"))  # flood: suppressed
        t[0] += 600
        self.assertTrue(lim.note_blocked("1.2.3.4"))   # next period: logged again


class TermTextSplit(unittest.TestCase):
    def test_runs_splitting_utf8_keep_whole_chars(self):
        import cards
        text = "가나다 ab …⏺ end\n"
        b = text.encode()
        cuts = [b.index("다".encode()) + 1, b.index("⏺".encode()) + 2, len(b)]

        class Vte:  # byte counts and run bounds that cut characters in half
            def get_character_count(self):
                return len(b)

            def get_text(self, *a):
                return text

            def get_attribute_run(self, off, dummy=False):
                return ({"fg-color": "34621,38036,30840"}, 0, next(c for c in cuts if c > off))

        cards._terms["t1.0"] = Vte()
        try:
            segs = cards.term_text("t1.0")
        finally:
            cards._terms.clear()
        joined = "".join(seg[0] for seg in segs)
        self.assertEqual(joined, text)  # tiling: nothing lost, nothing garbled
        self.assertNotIn("\ufffd", joined)


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = os.path.realpath(tempfile.mkdtemp())
        write(os.path.join(cls.root, "note.md"), "# Title\n\ntext")
        salt = secrets.token_bytes(16)
        server.H.cfg = {**config.DEFAULTS, "auth": "password", "files_root": cls.root,
                        "salt": salt.hex(), "hash": server.hash_password("pw-for-tests", salt)}
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        server.LIMITER = auth.Limiter(per_ip=3, global_max=10)

    def test_audit_log_lines(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            st, _, _ = self.req("POST", "/login", "pw=nope")
            self.assertEqual(st, 401)
            st, _, _ = self.req("POST", "/login", "pw=pw-for-tests")
            self.assertEqual(st, 303)
        self.assertIn("login failed from 127.0.0.1", out.getvalue())
        self.assertIn("login ok from 127.0.0.1", out.getvalue())

    def req(self, method, path, body=None, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=15)
        headers = {"Cookie": cookie} if cookie else {}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            c.request(method, path, body=body, headers=headers)
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def login(self, pw="pw-for-tests"):
        return self.req("POST", "/login", urllib.parse.urlencode({"pw": pw}))

    def cookie(self):
        status, headers, _ = self.login()
        self.assertEqual(status, 303)
        return headers["Set-Cookie"].split(";")[0]

    def test_unauthenticated(self):
        self.assertEqual(self.req("GET", "/")[0], 303)
        for path in ("/cards", "/files", "/theme", "/term?id=t1.0"):
            self.assertEqual(self.req("GET", path)[0], 401, path)
        self.assertEqual(self.req("POST", "/input", "{}")[0], 401)

    def test_wrong_password_counts_down(self):
        status, _, body = self.login("nope")
        self.assertEqual(status, 401)
        self.assertIn(b"2 tries left", body)

    def test_lockout_blocks_even_correct_password(self):
        for _ in range(3):
            self.login("nope")
        status, headers, body = self.login()  # correct password, but locked: never checked
        self.assertEqual(status, 429)
        self.assertIn(b"try again in 10 min", body)
        self.assertTrue(590 <= int(headers["Retry-After"]) <= 600, headers["Retry-After"])
        self.assertEqual(self.req("GET", "/login")[0], 429)

    def test_cookie_is_httponly_strict(self):
        status, headers, _ = self.login()
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])

    def test_forged_cookie(self):
        self.assertEqual(self.req("GET", "/files", cookie="wv=forged")[0], 401)

    def test_files_and_sandbox(self):
        cookie = self.cookie()
        status, _, body = self.req("GET", "/files?path=", cookie=cookie)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual([e["name"] for e in data["entries"]], ["note.md"])
        status, headers, body = self.req("GET", f"/f/{data['ftok']}/note.md")
        self.assertEqual(status, 200)
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertIn(b"<h1", body)
        self.assertEqual(self.req("GET", "/f/badtoken/note.md")[0], 401)
        self.assertEqual(self.req("GET", f"/f/{data['ftok']}/%2e%2e/%2e%2e/etc/passwd")[0], 404)
        self.assertEqual(self.req("GET", "/files?path=../..", cookie=cookie)[0], 404)

    def test_bad_card_id(self):
        self.assertEqual(self.req("GET", "/term?id=t1;ls", cookie=self.cookie())[0], 400)


if __name__ == "__main__":
    unittest.main()

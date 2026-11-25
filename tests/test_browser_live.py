"""The browser tools against a real Chrome: headless, on a debugging port of its
own, with a page of this test's own. Skipped where no Chrome or Chromium is."""
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import agent
import cards
import config

PAGE = """<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>swiv live</title>
<button id="go" onclick="out.textContent='clicked'">눌러라</button>
<input id="q" value="">
<p id="out">before</p>"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(cards.chrome_binary(), "no chrome or chromium")
class LiveBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="swiv-agent-live-")
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        cls.site.daemon_threads = True
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()
        cls.port = free_port()
        cls.chrome = subprocess.Popen(
            [cards.chrome_binary(), "--headless=new", "--no-sandbox", "--disable-gpu",
             "--remote-debugging-port=%d" % cls.port,
             "--user-data-dir=" + os.path.join(cls.dir, "profile"),
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                cards._cdp(cls.port, "/json/version").close()
                return
            except OSError:
                time.sleep(0.2)
        cls.tearDownClass()
        raise unittest.SkipTest("chrome did not open its debugging port")

    @classmethod
    def tearDownClass(cls):
        cls.chrome.terminate()
        try:
            cls.chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.chrome.kill()
        cls.site.shutdown()
        cls.site.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        desktop = {**config.DEFAULTS, "cdp_ports": [self.port],
                   "chrome_profile": os.path.join(self.dir, "profile"), "chrome_bin": cards.chrome_binary()}
        for patcher in (mock.patch.object(config, "load", return_value=desktop),
                        mock.patch.object(agent, "FILES_ROOT", self.dir)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cfg = dict(config.DEFAULTS["agent"])

    def tool(self, name, arguments):
        return agent.run_tool({"name": name, "arguments": arguments}, self.cfg)

    def open_page(self):
        url = "http://127.0.0.1:%d/" % self.site.server_address[1]
        out = self.tool("browser_open", {"url": url})
        self.assertTrue(out.startswith("탭을 열었다: " + url), out)
        found = re.search(r"\(카드 (p\d+\.[A-Fa-f0-9]+)\)", out)
        self.assertIsNotNone(found, out)  # the browser was up: the new tab can be named
        return found.group(1)

    def cards_until(self, needle):
        deadline = time.monotonic() + 10  # a tab lists under its url until its title has loaded
        while True:
            out = self.tool("cards_list", {})
            if needle in out or time.monotonic() > deadline:
                return out
            time.sleep(0.2)

    def read_until(self, needle, cid):
        deadline = time.monotonic() + 10  # the page may still be loading
        while True:
            out = self.tool("browser_read", {"id": cid})
            if needle in out or time.monotonic() > deadline:
                return out
            time.sleep(0.2)

    def test_open_read_and_drive_a_page(self):
        cid = self.open_page()
        self.assertTrue(cid.startswith("p%d." % self.port), cid)
        self.assertIn("page  %s  swiv live" % cid, self.cards_until("swiv live"))

        text = self.read_until("before", cid)
        self.assertIn("| before", text)
        self.assertIn("| 눌러라", text)
        self.assertNotIn("clicked", text)
        self.assertEqual(self.tool("browser_act", {"id": cid, "type": "click", "text": "눌러라"}),
                         "눌렀다: <button> 눌러라")
        self.assertIn("| clicked", self.read_until("clicked", cid))

        self.assertEqual(self.tool("browser_act", {"id": cid, "type": "fill", "selector": "#q", "value": "안 녕"}),
                         "넣었다: input = 안 녕")
        self.assertEqual(cards.page_eval(cid, "document.getElementById('q').value"), "안 녕")
        self.assertEqual(self.tool("browser_act", {"id": cid, "type": "click", "text": "없는 글"}), "찾지 못했다")

        for arguments, event in (({"type": "key", "key": "Enter"}, {"type": "key", "key": "Enter"}),
                                 ({"type": "scroll", "dy": -120}, {"type": "scroll", "x": 0, "y": 0, "dy": -120.0}),
                                 ({"type": "tap", "x": 5, "y": 5}, {"type": "tap", "x": 5.0, "y": 5.0}),
                                 ({"type": "text", "text": "héllo"}, {"type": "text", "text": "héllo"})):
            self.assertEqual(self.tool("browser_act", dict({"id": cid}, **arguments)),
                             "했다: " + json.dumps(event, ensure_ascii=False), arguments)
        gone = "p%d.%s" % (self.port, "0" * 32)
        self.assertTrue(self.tool("browser_read", {"id": gone}).startswith("error: 페이지에서"))
        self.assertTrue(self.tool("browser_act", {"id": gone, "type": "key", "key": "Enter"}).startswith("error:"))

    def test_the_phone_keeps_its_own_view_of_the_page(self):
        cid = self.open_page()
        self.read_until("before", cid)
        cards.page_open(cid, 390, 844, 2)  # the phone is on the card: emulated to its size
        self.addCleanup(cards.leave, cid)
        width = cards._pages[cid].call("Runtime.evaluate", {"expression": "innerWidth", "returnByValue": True})
        self.assertEqual(width["result"]["value"], 390)
        self.assertIn("| before", self.tool("browser_read", {"id": cid}))
        self.tool("browser_act", {"id": cid, "type": "click", "selector": "#go"})  # on the phone's own session
        self.assertIn(cid, cards._pages)  # and it is still the phone's
        width = cards._pages[cid].call("Runtime.evaluate", {"expression": "innerWidth", "returnByValue": True})
        self.assertEqual(width["result"]["value"], 390)
        self.assertIn("| clicked", self.tool("browser_read", {"id": cid}))


if __name__ == "__main__":
    unittest.main()

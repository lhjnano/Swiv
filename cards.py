# swiv card backends: gnome-terminal tabs via AT-SPI, browser pages via CDP.
# Viewing a card resizes the real terminal window (or emulates the phone viewport
# on the page) so programs redraw for the phone; leave/idle/exit restores it.
import base64
import bisect
import gettext
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import warnings

import websocket

import screen

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    SCREEN = Atspi.CoordType.SCREEN
except (ImportError, ValueError):  # no AT-SPI (Windows, macOS): no terminal cards
    Atspi = None

warnings.filterwarnings("ignore", category=DeprecationWarning)
LOCK = threading.RLock()  # AT-SPI and xdotool calls are not thread safe
IDLE_RESTORE = 20  # seconds without a frame poll before restore; server sets it from config
# gnome-terminal's own messages for the "close anyway?" button, translated through its catalog
CLOSE_MSGIDS = ("C_lose Terminal", "C_lose Window", "_Close Window", "Close")
CDP_HOST = "127.0.0.1"
CDP_TIMEOUT, CDP_LIST_TIMEOUT, CDP_WS_TIMEOUT = 2, 1, 10  # seconds
PASTE_DELAY = 0.2  # seconds for the terminal to read the clipboard before it is restored
DIALOG_POLL, DIALOG_TRIES = 0.2, 10  # wait for the close confirmation dialog
REAPER_SEC = 5
XKEYS = {"Enter": "Return", "C-c": "ctrl+c", "Escape": "Escape", "Tab": "Tab",
         "S-Tab": "shift+Tab", "Up": "Up", "Down": "Down", "Left": "Left",
         "Right": "Right", "BSpace": "BackSpace"}
CDPKEYS = {"Enter": ("Enter", 13, "\r"), "Escape": ("Escape", 27, ""), "Tab": ("Tab", 9, ""),
           "BSpace": ("Backspace", 8, ""), "Up": ("ArrowUp", 38, ""),
           "Down": ("ArrowDown", 40, ""), "Left": ("ArrowLeft", 37, ""),
           "Right": ("ArrowRight", 39, "")}

_terms = {}     # card id -> terminal accessible
_resized = {}   # xid -> {"w","h","max","tab","last"} original state while viewed
_pages = {}     # card id -> PageSession while viewed


def _cdp(port, path, method="GET", timeout=CDP_TIMEOUT):
    # scheme and host are fixed; only the integer port and the path vary
    req = urllib.request.Request(f"http://{CDP_HOST}:{int(port)}{path}", method=method)
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310


def _xdo(*args):
    return subprocess.run(["xdotool", *args], capture_output=True, text=True)


def _find(node, role, depth=0, out=None):
    out = [] if out is None else out
    if node is None or depth > 14:
        return out
    try:
        r = node.get_role_name()
    except Exception:
        return out
    if r == role:
        out.append(node)
        return out
    for i in range(node.get_child_count()):
        _find(node.get_child_at_index(i), role, depth + 1, out)
    return out


def _up(node, role):
    while node is not None:
        try:
            if node.get_role_name() == role:
                return node
            node = node.get_parent()
        except Exception:
            return None
    return None


def _windows():
    """gnome-terminal X windows: xid -> (title, (x, y, w, h))."""
    wins = {}
    for w in _xdo("search", "--class", "gnome-terminal").stdout.split():
        name = _xdo("getwindowname", w).stdout.rstrip("\n")
        g = dict(kv.split("=") for kv in _xdo("getwindowgeometry", "--shell", w).stdout.split())
        wins[w] = (name, (int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])))
    return wins


def _xid_of(frame, wins, used):
    """Match a terminal frame to its X window. Geometry alone is ambiguous: GNOME
    stacks new terminal windows at the same spot, so titles come first and a match
    must be unique among the not yet claimed windows."""
    name = frame.get_name() or ""
    e = frame.get_extents(SCREEN)
    geo = (e.x, e.y, e.width, e.height)
    for pred in (lambda v: v == (name, geo), lambda v: v[0] == name, lambda v: v[1] == geo):
        hits = [w for w, v in wins.items() if w not in used and pred(v)]
        if len(hits) == 1:
            return hits[0]
    twins = [w for w, v in wins.items() if w not in used and v == (name, geo)]
    return twins[0] if twins else None


def _terminal_cards():
    cards = []
    _terms.clear()
    if Atspi is None or not shutil.which("xdotool"):
        return cards  # terminal cards need X11 tooling; skip cleanly without it
    desk = Atspi.get_desktop(0)
    wins, used, memo = _windows(), set(), {}
    for i in range(desk.get_child_count()):
        app = desk.get_child_at_index(i)
        if not app or "terminal" not in (app.get_name() or "").lower():
            continue
        for t in _find(app, "terminal"):
            frame, tab = _up(t, "frame"), _up(t, "page tab")
            if frame is None:
                continue
            # tabs of one frame share the window; twins (same title and spot) share it too
            e = frame.get_extents(SCREEN)
            fkey = (frame.get_name() or "", e.x, e.y, e.width, e.height)
            xid = memo.get(fkey)
            if xid is None:
                xid = memo[fkey] = _xid_of(frame, wins, used)
                if xid:
                    used.add(xid)
            if not xid:
                continue
            idx = tab.get_index_in_parent() if tab else 0
            cid = f"t{xid}.{idx}"
            _terms[cid] = t
            title = tab.get_name() if tab and tab.get_name() else frame.get_name()
            cards.append({"kind": "term", "id": cid, "title": title})
    return cards


def _page_cards(ports):
    cards = []
    for port in ports:
        try:
            with _cdp(port, "/json/list", timeout=CDP_LIST_TIMEOUT) as f:
                targets = json.load(f)
        except (OSError, ValueError):
            continue
        for t in targets:
            if t.get("type") == "page":
                cards.append({"kind": "page", "id": f"p{int(port)}.{t['id']}",
                              "title": t.get("title") or t.get("url", "")})
    return cards


def list_cards(cfg):
    with LOCK:
        cards = _terminal_cards()
    return cards + screen.cards() + _page_cards(cfg["cdp_ports"])


# terminal cards -----------------------------------------------------------------

def _term(cid):
    t = _terms.get(cid)
    if t is None:
        _terminal_cards()
        t = _terms.get(cid)
    return t


def _select_tab(t):
    tab = _up(t, "page tab")
    if tab:
        tab.get_parent().select_child(tab.get_index_in_parent())


def _selected_tab_index(t):
    tab = _up(t, "page tab")
    sel = tab.get_parent().get_selected_child(0) if tab else None
    return sel.get_index_in_parent() if sel else 0


def term_open(cid, cols, rows):
    with LOCK:
        t = _term(cid)
        if t is None:
            return False
        xid = cid[1:].split(".")[0]
        if xid not in _resized:
            g = dict(kv.split("=") for kv in _xdo("getwindowgeometry", "--shell", xid).stdout.split())
            state = subprocess.run(["xprop", "-id", xid, "_NET_WM_STATE"],
                                   capture_output=True, text=True).stdout
            _resized[xid] = {"w": int(g["WIDTH"]), "h": int(g["HEIGHT"]),
                             "max": "MAXIMIZED" in state, "tab": _selected_tab_index(t)}
            if _resized[xid]["max"]:
                subprocess.run(["wmctrl", "-i", "-r", xid, "-b", "remove,maximized_vert,maximized_horz"])
        _resized[xid]["last"] = time.time()
        _select_tab(t)
        frame = _up(t, "frame").get_extents(SCREEN)
        te, cell = t.get_extents(SCREEN), t.get_character_extents(0, SCREEN)
        if cell.width <= 0 or cell.height <= 0:
            return False
        w = cols * cell.width + (frame.width - te.width)
        h = rows * cell.height + (frame.height - te.height)
        _xdo("windowsize", xid, str(w), str(h))
        return True


def _hex16(v):
    # VTE reports colors as "r,g,b" in 16-bit channels
    try:
        return "#%02x%02x%02x" % tuple(int(c) // 257 for c in v.split(","))
    except (AttributeError, ValueError):
        return None


def term_text(cid):
    """Visible screen as [[text, fg, bg], ...] color segments."""
    with LOCK:
        t = _term(cid)
        if t is None:
            return None
        xid = cid[1:].split(".")[0]
        if xid in _resized:
            _resized[xid]["last"] = time.time()
        # VTE counts character_count and attribute runs in UTF-8 bytes, and run
        # bounds can split a multi byte character: map every run to whole chars
        n = t.get_character_count()
        text = t.get_text(0, n)
        starts, b = [], 0
        for ch in text:
            starts.append(b)
            b += len(ch.encode("utf-8"))
        segs, off, i = [], 0, 0
        while off < b and i < len(starts):
            attrs, _, end = t.get_attribute_run(off, False)[:3]
            end = min(max(end, off + 1), b)
            j = max(bisect.bisect_right(starts, end - 1), i + 1)
            attrs = attrs or {}
            segs.append([text[i:j], _hex16(attrs.get("fg-color")), _hex16(attrs.get("bg-color"))])
            i, off = j, end
        return segs


def term_input(cid, text="", key=""):
    with LOCK:
        t = _term(cid)
        if t is None:
            return False
        _select_tab(t)
        _xdo("windowactivate", "--sync", cid[1:].split(".")[0])
        if text:
            _paste(text)
        if key in XKEYS:
            _xdo("key", "--clearmodifiers", XKEYS[key])
        return True


def term_new():
    if Atspi is None or not shutil.which("gnome-terminal"):
        return False
    subprocess.Popen(["gnome-terminal", "--window"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def term_close(cid):
    # ctrl+shift+w closes the tab; confirm gnome-terminal's "process running" dialog
    with LOCK:
        t = _term(cid)
        if t is None:
            return False
        _term_restore(cid[1:].split(".")[0])
        app = t.get_application()
        _select_tab(t)
        _xdo("windowactivate", "--sync", cid[1:].split(".")[0])
        _xdo("key", "--clearmodifiers", "ctrl+shift+w")
        for _ in range(DIALOG_TRIES):
            time.sleep(DIALOG_POLL)
            for dlg in _find(app, "alert") + _find(app, "dialog"):
                for b in _find(dlg, "push button"):
                    name = (b.get_name() or "").lower()
                    if any(label in name for label in close_labels()):
                        b.do_action(0)
                        return True
        return True


def close_labels():
    """Close button names in the desktop's language, lower case, mnemonics removed."""
    labels = set()
    for msgid in CLOSE_MSGIDS:
        for s in (msgid, gettext.dgettext("gnome-terminal", msgid)):
            labels.add(re.sub(r"\(_\w\)|_", "", s).strip().lower())
    return labels


def _paste(text):
    # xdotool type garbles text under ibus/hangul; paste via clipboard, then restore it
    clip = ["xclip", "-selection", "clipboard"]
    old = subprocess.run([*clip, "-o"], capture_output=True).stdout
    subprocess.run(clip, input=text.encode())
    _xdo("key", "--clearmodifiers", "ctrl+shift+v")
    time.sleep(PASTE_DELAY)
    if old:
        subprocess.run(clip, input=old)


def _term_restore(xid):
    st = _resized.pop(xid, None)
    if not st:
        return
    if st["max"]:
        subprocess.run(["wmctrl", "-i", "-r", xid, "-b", "add,maximized_vert,maximized_horz"])
    else:
        _xdo("windowsize", xid, str(st["w"]), str(st["h"]))
    for cid, t in list(_terms.items()):
        tab = _up(t, "page tab")
        if cid.startswith(f"t{xid}.") and tab and tab.get_index_in_parent() == st["tab"]:
            _select_tab(t)
            break


# page cards ---------------------------------------------------------------------

class PageSession:
    # one devtools connection per viewed page; emulation dies with the connection
    def __init__(self, port, target):
        with _cdp(port, "/json/list") as f:
            url = next(t["webSocketDebuggerUrl"] for t in json.load(f) if t.get("id") == target)
        self.ws = websocket.create_connection(url, timeout=CDP_WS_TIMEOUT, suppress_origin=True)
        self.lock, self.n, self.last = threading.Lock(), 0, time.time()

    def call(self, method, params=None):
        with self.lock:
            self.n += 1
            self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("id") == self.n:
                    return msg.get("result")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _page(cid):
    s = _pages.get(cid)
    if s:
        s.last = time.time()
    return s


def page_open(cid, width, height, dpr):
    port, target = cid[1:].split(".", 1)
    s = _pages.get(cid)
    if s is None:
        s = _pages[cid] = PageSession(int(port), target)
    s.last = time.time()
    s.call("Emulation.setDeviceMetricsOverride",
           {"width": width, "height": height, "deviceScaleFactor": dpr, "mobile": True})
    s.call("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
    return True


def page_shot(cid):
    s = _page(cid)
    if s is None:
        return None
    r = s.call("Page.captureScreenshot", {"format": "jpeg", "quality": 60})
    return base64.b64decode(r["data"]) if r and "data" in r else None


def page_input(cid, ev):
    s = _page(cid)
    if s is None:
        return False
    kind = ev.get("type")
    if kind == "tap":
        x, y = float(ev["x"]), float(ev["y"])
        for t in ("mousePressed", "mouseReleased"):
            s.call("Input.dispatchMouseEvent",
                   {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
    elif kind == "scroll":
        s.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": float(ev["x"]),
                                            "y": float(ev["y"]), "deltaX": 0, "deltaY": float(ev["dy"])})
    elif kind == "text":
        s.call("Input.insertText", {"text": str(ev["text"])})
    elif kind == "key" and ev.get("key") in CDPKEYS:
        k, code, txt = CDPKEYS[ev["key"]]
        base = {"key": k, "code": k, "windowsVirtualKeyCode": code}
        s.call("Input.dispatchKeyEvent", {"type": "keyDown", **base, **({"text": txt} if txt else {})})
        s.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})
    return True


CHROMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
           # Windows and macOS do not put Chrome on PATH
           os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
           os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium")


def chrome_binary(configured=""):
    if configured:
        return shutil.which(configured) or (configured if os.path.isfile(configured) else None)
    return next((b for b in map(shutil.which, CHROMES) if b), None) or \
        next((b for b in CHROMES if os.path.isabs(b) and os.path.isfile(b)), None)


def page_new(port, profile, url, chrome_bin=""):
    # new tab in the CDP browser; start a visible one on the desktop if none is up
    try:
        _cdp(port, "/json/new?" + urllib.parse.quote(url, safe=":/?&=#%"), method="PUT").close()
        return True
    except OSError:
        pass
    exe = chrome_binary(chrome_bin)
    if not exe:
        return False
    subprocess.Popen([exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                      "--no-first-run", "--no-default-browser-check", "--new-window", url],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def page_close(cid):
    port, target = cid[1:].split(".", 1)
    s = _pages.pop(cid, None)
    if s:
        s.close()
    _cdp(port, f"/json/close/{target}").close()
    return True


# shared -------------------------------------------------------------------------

def leave(cid):
    if cid.startswith("sh"):
        return  # shell cards outlive the phone on purpose
    with LOCK:
        if cid.startswith("t"):
            _term_restore(cid[1:].split(".")[0])
        elif cid.startswith("s"):
            screen.leave(cid)
        elif cid in _pages:
            _pages.pop(cid).close()


def restore_all():
    with LOCK:
        for xid in list(_resized):
            _term_restore(xid)
        for cid in list(_pages):
            _pages.pop(cid).close()


def reaper():
    # restore cards whose phone went away without calling /leave
    while True:
        time.sleep(REAPER_SEC)
        cutoff = time.time() - IDLE_RESTORE
        with LOCK:
            for xid in [x for x, st in _resized.items() if st.get("last", 0) < cutoff]:
                _term_restore(xid)
            for cid in [c for c, s in _pages.items() if s.last < cutoff]:
                _pages.pop(cid).close()

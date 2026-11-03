# swiv app cards: other desktop windows, read as text through the accessibility
# tree (AT-SPI on Linux). Read only for now; this module is also the engine slot
# where a different backend (agent-desktop on Windows and macOS) can plug in.
import shutil

import cards

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except (ImportError, ValueError):
    Atspi = None

MAX_DEPTH = 12

_apps = {}  # card id "a<xid>" -> frame accessible


def available(cfg):
    return bool(cfg.get("app_cards")) and Atspi is not None and shutil.which("xdotool")


def _excluded(name, cfg, title=""):
    n = (name or "").lower() + "\n" + (title or "").lower()
    return any(x.lower() in n for x in cfg.get("app_exclude", []))


def _windows():
    """All visible X windows: xid -> (title, (x, y, w, h))."""
    wins = {}
    for w in cards._xdo("search", "--onlyvisible", "--class", ".").stdout.split():
        name = cards._xdo("getwindowname", w).stdout.rstrip("\n")
        g = dict(kv.split("=") for kv in cards._xdo("getwindowgeometry", "--shell", w).stdout.split())
        wins[w] = (name, (int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])))
    return wins


def cards_list(cfg):
    """One read only card per visible window that no other card kind covers."""
    _apps.clear()
    out = []
    if not available(cfg):
        return out
    desk = Atspi.get_desktop(0)
    wins, used = _windows(), set()
    for i in range(desk.get_child_count()):
        app = desk.get_child_at_index(i)
        name = app.get_name() or ""
        if _excluded(name, cfg) or "terminal" in name.lower():
            continue
        for fr in cards._find(app, "frame"):
            if _excluded(name, cfg, fr.get_name() or ""):
                continue
            xid = cards._xid_of(fr, wins, used)
            if not xid:
                continue
            used.add(xid)
            cid = f"a{xid}"
            _apps[cid] = fr
            out.append({"kind": "app", "id": cid, "title": fr.get_name() or name})
    return out


def text(cid, cfg):
    """Window content as reading order lines, in the term card segment format."""
    fr = _apps.get(cid)
    if fr is None:
        return None
    cap = int(cfg.get("app_max_nodes", 400))
    lines = []

    def walk(node, depth):
        if len(lines) >= cap or depth > MAX_DEPTH:
            return
        try:
            role = node.get_role_name() or ""
            label = node.get_name() or ""
            if not label and role == "text":
                n = node.get_character_count()
                label = node.get_text(0, n) if n else ""
            if label.strip():
                lines.append("  " * depth + label.replace("\n", " ")[:200])
            for i in range(node.get_child_count()):
                walk(node.get_child_at_index(i), depth + 1)
        except Exception:
            return

    walk(fr, 0)
    return [[line, "", ""] for line in lines] or None

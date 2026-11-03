# swiv screen cards: the whole monitor as an image, for Windows and macOS where
# terminals cannot be read. Taps and keys go to the real mouse and keyboard.
# Linux keeps terminal cards only, so this module stays off there.
import io
import math
import sys
import threading

ENABLED = sys.platform in ("win32", "darwin")
JPEG_QUALITY = 60
SCROLL_PX = 40  # phone drag pixels per mouse wheel step
KEYS = {"Enter": "enter", "Escape": "esc", "Tab": "tab", "Up": "up", "Down": "down",
        "Left": "left", "Right": "right", "BSpace": "backspace"}

_lock = threading.Lock()
_open = {}  # card id -> phone width in pixels, while viewed
_local = threading.local()  # mss handles are per thread


def _mss():
    if not hasattr(_local, "sct"):
        import mss
        _local.sct = mss.mss()
    return _local.sct


def _monitor(cid):
    mons = _mss().monitors  # [0] is all monitors together, [1:] one each
    i = int(cid[1:])
    return mons[i] if 1 <= i < len(mons) else None


def cards():
    if not ENABLED:
        return []
    mons = _mss().monitors[1:]
    return [{"kind": "screen", "id": f"s{i}",
             "title": "screen" if len(mons) == 1 else f"screen {i}"}
            for i in range(1, len(mons) + 1)]


def open_(cid, width):
    with _lock:
        _open[cid] = width
    return _monitor(cid) is not None


def leave(cid):
    with _lock:
        _open.pop(cid, None)


def shot(cid):
    from PIL import Image
    mon = _monitor(cid)
    width = _open.get(cid)
    if mon is None or width is None:
        return None
    raw = _mss().grab(mon)
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    if img.width > width:  # send no more pixels than the phone shows
        img = img.resize((width, round(img.height * width / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _point(mon, ev):
    # the phone sends fractions of the image, so scaling on either side does not matter
    fx, fy = float(ev["fx"]), float(ev["fy"])
    if not (math.isfinite(fx) and math.isfinite(fy)):
        raise ValueError("bad point")
    fx, fy = min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0)
    return mon["left"] + fx * (mon["width"] - 1), mon["top"] + fy * (mon["height"] - 1)


def input_(cid, ev):
    from pynput import keyboard, mouse
    mon = _monitor(cid)
    if mon is None or cid not in _open:
        return False
    kind = ev.get("type")
    if kind in ("tap", "scroll"):
        m = mouse.Controller()
        m.position = _point(mon, ev)
        if kind == "tap":
            m.click(mouse.Button.left)
        else:
            m.scroll(0, -round(float(ev["dy"]) / SCROLL_PX))
    elif kind == "text":
        keyboard.Controller().type(str(ev["text"]))
    elif kind == "key":
        k = keyboard.Controller()
        key = ev.get("key")
        if key == "C-c":
            with k.pressed(keyboard.Key.ctrl):  # interrupt, as in a terminal
                k.tap("c")
        elif key == "S-Tab":
            with k.pressed(keyboard.Key.shift):
                k.tap(keyboard.Key.tab)
        elif key in KEYS:
            k.tap(getattr(keyboard.Key, KEYS[key]))
        else:
            return False
    return True

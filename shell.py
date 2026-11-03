# swiv shell cards: ptys owned by the server, so the phone can type even while
# the desktop is locked. No window and no X: pyte renders each screen and phone
# input is written straight into the pty. Spawned with "+" -> new shell.
import os
import select
import subprocess
import threading

import pyte

# how phone key names land in the pty
KEYS = {"Enter": "\n", "Escape": "\x1b", "Tab": "\t", "S-Tab": "\x1b[Z",
        "Up": "\x1b[A", "Down": "\x1b[B", "Left": "\x1b[D", "Right": "\x1b[C",
        "BSpace": "\x7f", "C-c": "\x03"}
NAMED = {"black": "#000000", "red": "#cd3131", "green": "#0dbc79", "brown": "#e5e510",
         "blue": "#2472c8", "magenta": "#bc3fbc", "cyan": "#11a8cd", "white": "#e5e5e5",
         "brightblack": "#666669", "brightred": "#f14c4c", "brightgreen": "#23d18b",
         "brightbrown": "#f5f543", "brightblue": "#3b8eea", "brightmagenta": "#d670d6",
         "brightcyan": "#29b8db", "brightwhite": "#ffffff"}

_lock = threading.Lock()
_shells = {}  # id like "sh3" -> {"proc", "master", "screen", "stream"}
_next = 0


def _winsize(fd, cols, rows):
    import fcntl
    import struct
    import termios
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _col(c):
    if not isinstance(c, str) or c == "default":
        return ""
    return NAMED.get(c, "#" + c if len(c) == 6 else "")


def _cleanup(sh):
    try:
        os.close(sh["master"])
    except OSError:
        pass


def cards():
    with _lock:
        out = []
        for sid, sh in list(_shells.items()):
            if sh["proc"].poll() is not None:
                _cleanup(sh)
                _shells.pop(sid, None)
                continue
            out.append({"kind": "shell", "id": sid, "title": "shell " + str(int(sid[2:]) + 1)})
        return out


def spawn(cmd=""):
    """Start a new shell; its card appears in the next card list."""
    global _next
    with _lock:
        sid = f"sh{_next}"
        _next += 1
        scr = pyte.Screen(80, 24)
        stream = pyte.ByteStream(scr)
        master, sfd = os.openpty()
        _winsize(master, 80, 24)
        proc = subprocess.Popen([cmd or os.environ.get("SHELL") or "/bin/bash"],
                                stdin=sfd, stdout=sfd, stderr=sfd, start_new_session=True)
        os.close(sfd)
        _shells[sid] = {"proc": proc, "master": master, "screen": scr, "stream": stream}

        def reader():
            while True:
                try:
                    if master not in select.select([master], [], [], 1.0)[0]:
                        continue
                    data = os.read(master, 65536)
                    if not data:
                        break
                    with _lock:
                        stream.feed(data)
                except OSError:
                    break

        threading.Thread(target=reader, daemon=True).start()
        return sid


def _get(cid):
    sh = _shells.get(cid)
    if sh and sh["proc"].poll() is not None:
        _cleanup(sh)
        _shells.pop(cid, None)
        return None
    return sh


def open_(cid, cols, rows, cmd=""):
    """Resize one shell to the phone's grid (spawn it if it died)."""
    sh = _get(cid)
    if sh is None:
        return False
    with _lock:
        _winsize(sh["master"], cols, rows)
        sh["screen"].resize(rows, cols)
    return True


def write(cid, data):
    sh = _get(cid)
    if sh is None:
        return False
    try:
        os.write(sh["master"], data.encode())
        return True
    except OSError:
        return False


def screen(cid):
    """The rendered screen as [text, fg, bg] segments, in the term card format."""
    sh = _get(cid)
    if sh is None:
        return None
    scr = sh["screen"]
    with _lock:
        out = []
        for y in range(scr.lines):
            fg = bg = None
            buf = []
            for x in range(scr.columns):
                ch = scr.buffer[y][x]
                if (_col(ch.fg), _col(ch.bg)) != (fg, bg):
                    if buf:
                        out.append(["".join(buf), fg or "", bg or ""])
                    buf, fg, bg = [], _col(ch.fg), _col(ch.bg)
                buf.append(ch.data)
            out.append(["".join(buf) + "\n", fg or "", bg or ""])
        return out


def close(cid):
    """End one shell; its card disappears from the next card list."""
    with _lock:
        sh = _shells.pop(cid, None)
    if sh:
        sh["proc"].terminate()
        try:
            sh["proc"].wait(2)
        except subprocess.TimeoutExpired:
            sh["proc"].kill()
        _cleanup(sh)

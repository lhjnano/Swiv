# swiv file card: list a root folder and serve files for the phone viewer.
# Every response is CSP-sandboxed, so a viewed html/md document gets an opaque origin
# and cannot call swiv's input APIs with the login cookie.
import html
import mimetypes
import os

import markdown

SANDBOX = "sandbox allow-scripts allow-popups allow-forms"
MD_EXT = ["extra", "sane_lists", "toc", "codehilite"]
MD_CSS = """body{margin:0 auto;max-width:860px;padding:14px 16px 40px;font:15px/1.6 system-ui,
sans-serif;color:#1f2328;background:#fff;word-wrap:break-word}
pre{overflow-x:auto;background:#f6f8fa;padding:10px;border-radius:6px;font-size:13px}
code{font:13px ui-monospace,monospace;background:#f6f8fa;padding:1px 4px;border-radius:4px}
pre code{padding:0;background:none}table{border-collapse:collapse;display:block;overflow-x:auto}
th,td{border:1px solid #d0d7de;padding:5px 9px}img{max-width:100%}
blockquote{margin:0;padding:0 12px;color:#59636e;border-left:4px solid #d0d7de}
h1,h2{border-bottom:1px solid #d8dee4;padding-bottom:4px}a{color:#0969da}"""


def root_of(cfg):
    return os.path.realpath(os.path.expanduser(cfg["files_root"]))


def resolve(root, rel):
    # realpath + commonpath blocks ../ and symlinks that leave the root
    p = os.path.realpath(os.path.join(root, rel.lstrip("/")))
    return p if os.path.commonpath([p, root]) == root else None


def listing(root, rel):
    p = resolve(root, rel)
    if not p or not os.path.isdir(p):
        return None
    entries = []
    with os.scandir(p) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                st = e.stat()
                is_dir = e.is_dir()
            except OSError:
                continue
            entries.append({"name": e.name, "dir": is_dir, "size": st.st_size, "mtime": int(st.st_mtime)})
    entries.sort(key=lambda e: (not e["dir"], -e["mtime"] if not e["dir"] else 0, e["name"].lower()))
    return {"path": "" if p == root else os.path.relpath(p, root), "entries": entries}


def _looks_text(path):
    with open(path, "rb") as f:
        return b"\0" not in f.read(4096)


def serve(handler, root, rel, raw):
    """Send one file; .md is rendered unless raw. Returns False if not servable."""
    p = resolve(root, rel)
    if not p or not os.path.isfile(p):
        return False
    headers = [("Content-Security-Policy", SANDBOX), ("X-Content-Type-Options", "nosniff")]
    if p.lower().endswith((".md", ".markdown")) and not raw:
        with open(p, encoding="utf-8", errors="replace") as f:
            body = markdown.markdown(f.read(), extensions=MD_EXT)
        title = html.escape(os.path.basename(p))
        page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,"
                f"initial-scale=1'><title>{title}</title><style>{MD_CSS}</style>{body}")
        handler._send(200, "text/html; charset=utf-8", page.encode(), headers)
        return True
    ctype = mimetypes.guess_type(p)[0] or ""
    if not ctype.startswith(("image/", "text/html", "application/pdf", "video/", "audio/")):
        ctype = "text/plain; charset=utf-8" if _looks_text(p) else "application/octet-stream"
    elif ctype == "text/html":
        ctype = "text/html; charset=utf-8"
    size = os.path.getsize(p)
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(size))
    handler.send_header("Cache-Control", "no-store")
    for k, v in headers:
        handler.send_header(k, v)
    handler.end_headers()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            handler.wfile.write(chunk)
    return True

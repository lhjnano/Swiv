# swiv theme: mirror the desktop wallpaper and the default gnome-terminal profile.
# Values are read from gsettings on each request so desktop changes follow automatically.
import os
import re
import subprocess
import urllib.parse

_cache = {}  # (path, mtime, width) -> downscaled wallpaper jpeg


def _run(*cmd):
    # gsettings, fc-match and ffmpeg may be missing (Windows, macOS): no theme then
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _gs(*args):
    return _run("gsettings", "get", *args).strip("'")


def _rgb(s):
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", s or "")
    return "#%02x%02x%02x" % tuple(map(int, m.groups())) if m else None


def _profile():
    uuid = _gs("org.gnome.Terminal.ProfilesList", "default")
    schema = f"org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:{uuid}/"
    return lambda key: _gs(schema, key)


def font_spec():
    p = _profile()
    spec = _gs("org.gnome.desktop.interface", "monospace-font-name") if p("use-system-font") == "true" else p("font")
    m = re.match(r"(.*?)\s+([\d.]+)$", spec)
    return (m.group(1), float(m.group(2))) if m else (spec or "Monospace", 12.0)


def font_path():
    family, _ = font_spec()
    path = _run("fc-match", family, "-f", "%{file}")
    # .ttc collections are not loadable as a web font
    return path if path.lower().endswith((".ttf", ".otf", ".woff", ".woff2")) and os.path.isfile(path) else None


def wallpaper_path():
    dark = _gs("org.gnome.desktop.interface", "color-scheme") == "prefer-dark"
    uri = _gs("org.gnome.desktop.background", "picture-uri-dark" if dark else "picture-uri")
    if not uri.startswith("file://"):
        return None
    path = urllib.parse.unquote(uri[len("file://"):])
    return path if os.path.isfile(path) else None


def wallpaper_jpeg(width):
    path = wallpaper_path()
    if not path:
        return None
    key = (path, os.path.getmtime(path), width)
    if key not in _cache:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-frames:v", "1",
                            "-vf", f"scale='min({width},iw)':-2", "-q:v", "5", "-f", "mjpeg", "pipe:1"],
                           capture_output=True)
        if r.returncode != 0 or not r.stdout:
            return None
        _cache.clear()
        _cache[key] = r.stdout
    return _cache[key]


def summary():
    p = _profile()
    themed = p("use-theme-colors") != "true"
    alpha = 1.0
    if p("use-transparent-background") == "true":
        alpha = max(0.0, 1 - int(p("background-transparency-percent") or 0) / 100)
    family, size = font_spec()
    bg = _rgb(p("background-color")) if themed else None
    return {"fg": (_rgb(p("foreground-color")) if themed else None) or "#e6e6e6",
            "bg": bg or "#0d0f12", "alpha": round(alpha, 2),
            "font": family, "font_px": round(size * 96 / 72, 1), "has_font": bool(font_path()),
            "wallpaper": bool(wallpaper_path()),
            "wallpaper_mode": _gs("org.gnome.desktop.background", "picture-options") or "zoom",
            "wallpaper_color": _gs("org.gnome.desktop.background", "primary-color") or "#000000"}

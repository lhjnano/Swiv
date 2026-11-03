# swiv settings: config file location, defaults, load and save.
import json
import os
import sys

_XDG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
PATH = os.environ.get("SWIV_CONFIG", os.path.join(_XDG, "swiv", "config.json"))
# every setting can be overridden in the config file; see config.example.json
DEFAULTS = {
    # the phone must reach the server over the network; narrow it in config (for example a VPN address)
    "bind": "0.0.0.0",  # nosec B104
    "port": 6090,
    "display": ":0",
    "files_root": "~",
    "cdp_ports": [9222],
    "chrome_bin": "",
    "chrome_profile": os.path.join(_XDG, "swiv", "chrome"),
    "shell": "",  # program of the shell cards; empty = $SHELL, then bash
    "desk_card": True,  # the desktop overview card
    "app_cards": True,  # read only cards for other app windows (accessibility)
    "app_exclude": ["chrom", "gnome-terminal", "gnome-shell", "@!"],  # name/title parts
    "app_max_nodes": 400,  # accessibility nodes flattened per app card
    # set both to serve HTTPS (see docs/https.md); empty keeps plain HTTP
    "cert": "",
    "key": "",
    "idle_restore_sec": 20,
    "session_days": 7,
    # "pam": this account's system password, "password": --set-password (Windows has no PAM)
    "auth": "password" if sys.platform == "win32" else "pam",
    "pam_service": "login",
    "pam_dir": "/etc/pam.d",  # scanned for lockout modules (pam_faillock, pam_tally)
    "wallpaper_width": 1440,  # px; the wallpaper is downscaled to this before sending
    # login throttling, checked before PAM so failures never reach an account lockout
    "login_max_failures": 5,          # per client IP within the window
    "login_global_max_failures": 20,  # all IPs together within the window
    "login_window_min": 15,
    "login_lockout_min": 10,
    "allowed_countries": [],  # ISO codes, for example ["KR"]; empty = no country filter
}


def read_file():
    """Keys set in the config file only ({} when there is no file)."""
    try:
        with open(PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def load():
    return {**DEFAULTS, **read_file()}


def save(cfg):
    """Write the config file, readable by the owner only (it may hold a password hash)."""
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    fd = os.open(PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

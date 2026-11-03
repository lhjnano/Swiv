# Server setup

The server reads windows through X11 tools (xdotool, wmctrl) and
gnome-terminal's accessibility interface, so it only runs on **Linux with a
GNOME-family desktop in an X11 session**.

| Server OS | Supported |
|---|---|
| Debian, Ubuntu and derivatives | yes, `make deps` (tested on 22.04) |
| Fedora, RHEL family | should work, packages by hand (untested) |
| Arch Linux | should work, packages by hand (untested) |
| Any Linux on Wayland | no, log in to an X11 ("Xorg") session instead |
| Windows, macOS | screen card instead of terminals, see below (untested) |

Some recent releases ship GNOME without an X11 session at all. On those swiv
cannot run. Windows and macOS have no gnome-terminal to read, so there swiv
shows the whole screen instead: [Windows](server-windows.md),
[macOS](server-macos.md).

## 1. Check the session type

```
echo $XDG_SESSION_TYPE
```

It must print `x11`. If it prints `wayland`, log out, pick the "GNOME on Xorg"
session with the gear icon on the login screen, and log in again.

## 2. Install system packages

Debian and Ubuntu:

```
make deps
```

Fedora and the RHEL family (`ffmpeg-free` is in Fedora; on RHEL enable EPEL or
RPM Fusion):

```
sudo dnf install python3-gobject at-spi2-core glib2 xdotool wmctrl xprop xclip \
  ffmpeg-free fontconfig gnome-terminal
```

Arch Linux:

```
sudo pacman -S python-gobject at-spi2-core glib2 xdotool wmctrl xorg-xprop \
  xclip ffmpeg fontconfig gnome-terminal
```

For browser cards also install Google Chrome or Chromium. Without one, only the
browser cards are missing.

## 3. Install swiv

The same on every distribution:

```
make check      # reports anything still missing
make install    # venv, config file, accessibility on, user service
make start
make status
```

If `make check` warns about `pam_faillock` (the default on Fedora and RHEL),
keep `login_global_max_failures` in `~/.config/swiv/config.json` below
faillock's `deny` count (see `/etc/security/faillock.conf`), so swiv's own
lockout always triggers first.

Next: open the port and reach it from the client in
[network access](network.md).

Back to the [README](../README.md).

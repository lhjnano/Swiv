# swiv: desktop terminals, browsers and files on your phone

A small web server that lets you flip through the terminals, Chrome tabs and
documents of a GNOME (X11) desktop as cards in a phone browser, and drive them
from there. Unlike a remote desktop that streams cropped screen pixels, swiv
takes the content and redraws it for the phone.

- Terminal cards: text and colors of each gnome-terminal tab, read through the
  accessibility interface (AT-SPI). While you view a card, the real terminal
  window is resized to the phone's columns and rows, so the program inside
  redraws for the phone width.
- Browser cards: tabs of a Chrome started with a remote debugging port (CDP).
  While you view a card, the phone viewport is emulated so the page redraws in
  its mobile layout.
- File card: browse a folder and open md (rendered), html, images, PDF and text
  files.
- The desktop wallpaper and the default terminal profile (colors, transparency,
  font) are mirrored.

Leaving a card, 20 seconds without a phone poll, or stopping the server restores
window sizes and viewports.

## Requirements

- Linux desktop, GNOME family, **X11 session** (Wayland is not supported:
  xdotool and window resizing are X11 only)
- gnome-terminal, Python 3.8+
- Chrome or Chromium for browser cards (without it only those cards are missing)

Windows and macOS get a reduced mode: instead of terminal cards there is a
screen card that shows the whole monitor and sends taps and keys to it. Browser
and file cards work the same. See [Windows](docs/server-windows.md) and
[macOS](docs/server-macos.md); both are untested.

## Install

Step by step setup for each OS is in the documents listed at the end. The
short version for Debian and Ubuntu:

```
git clone <this repository> swiv && cd swiv
make deps       # system packages (Debian/Ubuntu, needs sudo)
make install    # deps check, venv, config, accessibility, user service
make start
```

Open `http://<desktop IP>:6090/` on the phone and log in with **the desktop
account's password**. On Android Chrome, "Add to Home screen" turns it into an
app-like shortcut.

If `make install` had to turn accessibility (`toolkit-accessibility`) on,
terminals that were already open may not show up as cards. Open a new terminal,
or log out and back in once.

On other distributions install the equivalent packages instead of `make deps`:

```
python3 venv, PyGObject (gi) + AT-SPI 2 typelib, at-spi2-core, gsettings,
xdotool, wmctrl, xprop, xclip, ffmpeg, fontconfig (fc-match), gnome-terminal
```

## Documentation

- [Server setup](docs/server.md): per Linux distribution
- Server on [Windows](docs/server-windows.md) or [macOS](docs/server-macos.md)
- [Network access](docs/network.md): firewall, address, VPN
- [Client setup](docs/client.md): Android, iPhone and iPad, desktop browsers
- [Commands and configuration](docs/configuration.md)
- [HTTPS and the installable app](docs/https.md)
- [Usage](docs/usage.md): gestures and buttons
- [Security](docs/security.md): login, throttling, sandbox, network
- [Troubleshooting](docs/troubleshooting.md), uninstall
- [Development](docs/development.md): layout, tests, CI

## License

MIT, see [LICENSE](LICENSE).

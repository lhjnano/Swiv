# Client setup

Open `http://<server address>:6090/` and log in with the password of the account
that runs the server.

| Client | Status |
|---|---|
| Android, Chrome | tested, the main target |
| iPhone and iPad, Safari | untested, should work (touch based) |
| Windows, macOS, Linux desktop browsers | view only, see below |

## Android (Chrome)

1. Join the same network (Wi-Fi or VPN) as the server.
2. Open the address in Chrome and log in.
3. Over HTTPS an `install` button appears in the header; the installed app
opens without a browser bar and shows a connect notice when the desktop
is unreachable. Over HTTP use the browser menu's "Add to Home screen"
instead (a plain shortcut).

Chrome may label the page "Not secure" because it is plain HTTP. That is
expected on a trusted network or VPN.

## iPhone and iPad (Safari)

1. Join the same network or VPN.
2. Open the address in Safari and log in.
3. Optional: Share → "Add to Home Screen".

## Windows, macOS and Linux desktop browsers

Swiping between cards uses touch events, so a mouse cannot switch cards. Two
options:

- Open the browser's developer tools and turn on device emulation (Chrome and
  Edge: F12 → "Toggle device toolbar", Firefox: Ctrl+Shift+M and turn on touch
  simulation). Mouse drags then act as swipes.
- Use a real remote desktop client instead if you want the whole screen.

Login, the `+` and `×` buttons, the input box and the file card work with a
mouse.

Back to the [README](../README.md).

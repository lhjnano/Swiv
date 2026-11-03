# Server on macOS

Untested. macOS has no gnome-terminal to read, so swiv shows a **screen
card** instead: the whole monitor as an image, with taps and keys sent to the
real mouse and keyboard. Browser cards (Chrome) and the file card work as on
Linux.

## 1. Install Python

Python 3.8 or newer, from python.org or Homebrew (`brew install python`).

## 2. Install swiv

Inside the swiv folder:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The config file is `~/.config/swiv/config.json`. Set `files_root` there to
the folder the file card should show.

Login uses your macOS account password through PAM by default. If that does
not work, switch to a separate swiv password:

```
.venv/bin/python server.py --set-password
```

## 3. Run it and grant permissions

```
.venv/bin/python server.py
```

The first screenshot and the first tap ask for permissions. In System Settings
→ Privacy & Security allow the Python that runs swiv under both
**Screen Recording** and **Accessibility**, then restart swiv. Without them
the screen card stays empty or taps do nothing.

## 4. Network

macOS asks whether Python may accept incoming connections: allow it. Find the
address with `ipconfig getifaddr en0` (Wi-Fi) and read
[network access](network.md) about using a VPN.

## 5. Start at login (optional)

Save this as `~/Library/LaunchAgents/swiv.plist`, with both paths replaced
by the real ones:

```
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>swiv</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/swiv/.venv/bin/python</string>
    <string>/path/to/swiv/server.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

Then `launchctl load ~/Library/LaunchAgents/swiv.plist`. Remove it with
`launchctl unload` on the same file and delete the file.

## Browser cards

Chrome and Chromium are found in `/Applications`. If yours is elsewhere, set
`chrome_bin` in the config to the full path of the binary inside the app.

## Limits

- Text on the screen card is small: the whole monitor is shrunk to the phone
  width.
- `^C` sends Control+C (interrupt in a terminal), not Command+C (copy).

Back to the [README](../README.md).

# Troubleshooting

- **Terminals do not show up as cards**: if accessibility was off, open new
  terminals after `make install`. `gsettings get org.gnome.desktop.interface
  toolkit-accessibility` must be `true`.
- **New windows do not appear on the desktop**: a second session such as xrdp
  changed the user environment's `DISPLAY`. Check with `systemctl --user
  show-environment | grep DISPLAY`; if it is wrong, run `systemctl --user
  set-environment DISPLAY=:0` and `make restart`. Do not end the other session
  with `loginctl terminate-session`: it can log out your current session too.
  Log out from inside that session instead.
- **Colors look one character off after Korean or other multi byte text**:
  VTE reports color run bounds in UTF-8 bytes and they can fall inside a
  character; swiv rounds every run to whole characters, so a boundary may
  sit one character off. The text itself is never split or garbled.
- **Typed text gets mixed up**: `xdotool type` conflicts with Korean and other
  input methods, so swiv puts the text on the clipboard and pastes it with
  Ctrl+Shift+V (the previous clipboard is restored).
- **No browser cards**: that Chrome must run with
  `--remote-debugging-port=9222`. Starting one with `+` → new browser on the
  phone does that automatically.
- Logs: `make logs`.

# Uninstall

```
make uninstall
rm -rf ~/.config/swiv   # config and the dedicated Chrome profile
```

Back to the [README](../README.md).

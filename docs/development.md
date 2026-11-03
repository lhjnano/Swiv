# Development

# Layout

```
server.py   authentication, throttling hook-up, HTTP routes
config.py   config file location, defaults, load and save
auth.py     throttling (Limiter), PAM lockout module detection
screen.py   screen cards for Windows and macOS (mss, Pillow, pynput)
cards.py    terminal (AT-SPI, xdotool) and browser (CDP) cards, resize, restore
files.py    file listing and serving, md rendering, sandbox
geo.py      country allow list (RIR range data, cached weekly)
theme.py    wallpaper, terminal profile colors and font (gsettings)
page.html   phone UI (single file)
```

# CI

`.github/workflows/ci.yml` runs `make test` and `make security` on every push
and pull request. The scanners are pinned in `requirements-dev.txt` so results
do not change under you. Findings that are intended carry a `# nosec` comment
with the reason next to it.

Back to the [README](../README.md).

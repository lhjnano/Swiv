# Commands and configuration

## Commands

```
make help       list of commands
make check      dependencies and PAM lockout module check
make password   set the separate password (auth=password mode)
make start | stop | restart | status | logs
make run        run in the foreground (debugging)
make test       self tests
make security   bandit (medium and above) and pip-audit
make uninstall  remove the user service (config is kept)
```

The service is a systemd user unit (`~/.config/systemd/user/swiv.service`)
bound to the graphical session: it starts when you log in and stops when you log
out.

## Configuration

`~/.config/swiv/config.json` (mode 600). Missing keys use the defaults; the
full list is in `config.example.json`. Run `make restart` after changing it.

- `bind`, `port` (default `0.0.0.0`, `6090`): listen address and port
- `display` (default `:0`): X display for new windows
- `files_root` (default `~`): top folder of the file card (nothing outside it is
  reachable)
- `cdp_ports` (default `[9222]`): Chrome debugging ports picked up as browser
  cards
- `chrome_bin` (default auto): browser started by "new browser" (empty:
  google-chrome, then chromium)
- `chrome_profile` (default `~/.config/swiv/chrome`): dedicated profile for
  that browser
- `idle_restore_sec` (default `20`): seconds without a phone poll before window
  sizes are restored
- `session_days` (default `7`): how long a login stays valid
- `auth` (default `pam`, on Windows `password`): `pam`: account password,
  `password`: separate password from `make password`
- `pam_service` (default `login`): PAM service name (a file under `pam_dir`)
- `pam_dir` (default `/etc/pam.d`): where PAM service files are read from
  when checking for lockout modules
- `login_max_failures` (default `5`): failures allowed per client IP within the
  window
- `login_global_max_failures` (default `20`): failures allowed for all IPs
  together within the window
- `login_window_min` (default `15`): window for counting failures, in minutes
- `login_lockout_min` (default `10`): how long logins are blocked after a limit
  is hit, in minutes
- `allowed_countries` (default `[]`): ISO codes of countries allowed to connect,
  for example `["KR"]`; empty disables the filter. Local networks always pass
- `wallpaper_width` (default `1440`): the wallpaper is downscaled to this
  width in pixels before it is sent

Back to the [README](../README.md).

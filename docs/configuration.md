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
- `cert`, `key` (default `""`): set both to serve HTTPS;
  see [https.md](https.md)
- `shell` (default `""`): program run in shell cards; empty uses $SHELL,
  then bash
- `desk_card` (default `true`): show the desktop overview card
- `app_cards` (default `true`): show read only cards for other app windows
- `app_exclude` (default `["chrom", "gnome-terminal", "gnome-shell", "@!"]`):
  app name or window title parts that never become app cards
- `app_max_nodes` (default `400`): accessibility nodes flattened per app card
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
- `agent` (default: off): the agent chat card, [agent-card.md](agent-card.md).
  An object with these keys:
  - `enabled` (default `false`): show the card and its endpoints
  - `base_url`, `api_key`, `model` (default empty): an OpenAI compatible
    endpoint, for example `http://127.0.0.1:11434/v1` with `llama3` (ollama)
    or `https://api.openai.com/v1` with `gpt-4o-mini`. The key stays in this
    file and out of every log
  - `max_tokens`, `temperature` (default `2048`, `0.3`)
  - `allow_commands` (default ls, cat, head, tail, grep, find, df, du, ps,
    uname, uptime, pwd, which, git status, git log, git diff, git show): run
    at once, without a shell
  - `block_commands` (default sudo, doas, pkexec, su, mkfs, fdisk, sfdisk,
    parted, wipefs, shred, dd, shutdown, reboot, halt, poweroff, init,
    systemctl, rm, chmod, chown, chattr, mv, kill, killall, pkill, crontab,
    passwd, userdel, usermod): never asked, only refused; every `mkfs.*` goes
    with `mkfs`
  - `approval_timeout_sec`, `shell_timeout_sec` (default `120`, `60`)
  - `daily_token_budget` (default `0`, no limit): model tokens a local day may
    use. A stream then asks the provider for its usage (`stream_options`)
  - `rules_path` (default `~/.config/swiv/agent-rules.md`): what was taught
    with `/rule` and `/fix`, read into every prompt
  - `schedule` (default `[]`): jobs the server starts by itself in the chat,
    at a local time or every so often, for example
    `[{"name": "briefing", "at": "07:30", "days": [1, 2, 3, 4, 5], "text": "..."}]`
    or `{"name": "status", "every_min": 120, "text": "..."}`
  - `coach`: knowledge from the opencode usage-coach harness, read only:
    `enabled` (default `false`), `nodes_path`, `rules_path` (default empty:
    every project's rules.md under that harness's cache), `max_chars`
    (default `6000`)

`python3 agent.py --rules | --rule "<rule>" | --schedule | --once "<request>"`
works at the command line on the same config: list or add rules, check the
schedule, or run one request without a phone (what needs an approval is not
done).

Back to the [README](../README.md).

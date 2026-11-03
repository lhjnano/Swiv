# Security

- Login uses the PAM account password by default. The server runs as a normal
  user, so PAM can only verify **the account that runs the server**. No other
  account can log in.
- The login cookie is HttpOnly and SameSite=Strict. Sessions live in memory, so
  a server restart logs everyone out.
- Throttling is checked **before** PAM. Attempts over the limit are not checked
  at all, so the account never reaches a PAM lockout (`pam_faillock` and the
  like) and logging in works again once the lockout expires. Only one password
  check runs at a time, which also stops parallel guessing.
- On distributions with `pam_faillock` enabled (for example the RHEL family)
  `make check` and the server start log print a warning. Keep
  `login_global_max_failures` below faillock's `deny`.
- Documents of the file card open in an origin isolated by a CSP sandbox, so
  scripts in an html document cannot use the login cookie to call APIs such as
  terminal input. Files are served only with a read-only token issued at login
  and cannot leave `files_root` (`../` and symlinks included). Names starting
  with a dot are hidden from listings.
- Login attempts are audited: `login ok`, `login failed` and `login blocked`
  lines with a timestamp and the client IP go to the service journal
  (`make logs`), or to the output of a manual run. A blocked client logs one
  line per lockout period, not one per attempt, so a flood cannot fill the disk.
- An optional country filter (`allowed_countries` in the config) refuses
  connections from outside the listed countries before the login page, using the
  public RIR range data refreshed weekly. It cuts foreign scanners, but cannot
  stop someone renting an address inside an allowed country.
- Traffic is **plain HTTP**. Anyone on the same network can sniff the login
  password, so use it on a trusted network or inside a VPN (Tailscale and the
  like), and otherwise narrow `bind` to the VPN address.
- The browser cards' debugging port (9222) only listens locally, but any program
  on this machine can drive that Chrome. That is why "new browser" uses a
  dedicated profile separate from your everyday one.

Back to the [README](../README.md).

# swiv: run `make help`
SHELL := /bin/bash
# venv uses system site-packages for PyGObject; keep ~/.local out so installs are reproducible
export PYTHONNOUSERSITE := 1
DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
VENV := $(DIR)/.venv
PY := $(VENV)/bin/python
CONFIG_HOME := $(or $(XDG_CONFIG_HOME),$(HOME)/.config)
CONFIG := $(or $(SWIV_CONFIG),$(CONFIG_HOME)/swiv/config.json)
UNIT := $(CONFIG_HOME)/systemd/user/swiv.service

# Debian/Ubuntu package names; see README for other distributions
APT_PKGS := python3-venv python3-gi gir1.2-atspi-2.0 at-spi2-core libglib2.0-bin \
	xdotool wmctrl x11-utils xclip ffmpeg fontconfig gnome-terminal
CMDS := python3 xdotool wmctrl xprop xclip ffmpeg fc-match gsettings gnome-terminal
BROWSERS := google-chrome google-chrome-stable chromium chromium-browser

.PHONY: help check deps venv config password a11y service install run start stop restart \
	status logs test security uninstall clean

help:
	@echo "make deps       install system packages (apt, needs sudo)"
	@echo "make install    check deps, create venv and config, enable accessibility, register user service"
	@echo "make password   set or change the login password"
	@echo "make start|stop|restart|status|logs   control the user service"
	@echo "make run        run in the foreground (for debugging)"
	@echo "make test       run the self tests"
	@echo "make security   static analysis (bandit) and dependency audit (pip-audit)"
	@echo "make uninstall  remove the user service (config is kept)"

check:
	@missing=0; \
	for c in $(CMDS); do command -v $$c >/dev/null || { echo "missing command: $$c"; missing=1; }; done; \
	python3 -c 'import gi; gi.require_version("Atspi", "2.0"); from gi.repository import Atspi' 2>/dev/null \
	  || { echo "missing python module: gi + Atspi typelib"; missing=1; }; \
	browser=""; for b in $(BROWSERS); do command -v $$b >/dev/null && { browser=$$b; break; }; done; \
	[ -n "$$browser" ] && echo "browser: $$browser" || echo "warning: no chrome/chromium, browser cards disabled"; \
	[ "$${XDG_SESSION_TYPE:-x11}" = x11 ] || echo "warning: session is $$XDG_SESSION_TYPE, swiv needs X11"; \
	mods=$$(SWIV_CONFIG="$(CONFIG)" python3 -c 'import sys; sys.path.insert(0, "$(DIR)"); \
	  import auth, config; c = config.load(); \
	  m = auth.pam_lockout_modules(c["pam_service"], c["pam_dir"]) if c["auth"] == "pam" else []; \
	  print(c["pam_service"] + ": " + " ".join(m) if m else "")'); \
	[ -n "$$mods" ] && echo "warning: PAM service $$mods; keep login_global_max_failures below its deny count" || true; \
	if [ $$missing = 1 ]; then echo "run: make deps"; exit 1; fi; echo "dependencies ok"

deps:
	sudo apt-get install -y $(APT_PKGS)

venv: $(VENV)/.installed

# stamp file: .venv/bin/python is a symlink to the system python and must not be touched
$(VENV)/.installed: requirements.txt
	python3 -m venv --system-site-packages $(VENV)
	$(VENV)/bin/pip install -q -r requirements.txt
	touch $@

config:
	@mkdir -p $(dir $(CONFIG))
	@if [ -f "$(CONFIG)" ]; then echo "keeping $(CONFIG)"; \
	else install -m 600 config.example.json "$(CONFIG)"; echo "created $(CONFIG)"; fi

password: venv
	@SWIV_CONFIG="$(CONFIG)" $(PY) server.py --set-password

a11y:
	@if [ "$$(gsettings get org.gnome.desktop.interface toolkit-accessibility)" != true ]; then \
	  gsettings set org.gnome.desktop.interface toolkit-accessibility true; \
	  echo "enabled GNOME accessibility; restart terminals (or log out and in) once"; \
	else echo "accessibility already on"; fi

service: venv
	@mkdir -p $(dir $(UNIT))
	sed -e 's|@PYTHON@|$(PY)|g' -e 's|@DIR@|$(DIR)|g' swiv.service.in > "$(UNIT)"
	systemctl --user daemon-reload
	systemctl --user enable swiv.service

install: check venv config a11y service
	@echo; echo "next: make password && make start"

run: venv
	SWIV_CONFIG="$(CONFIG)" $(PY) server.py

start stop restart status:
	systemctl --user $@ swiv.service

logs:
	@if systemctl --user is-active --quiet swiv.service; then \
	  journalctl --user -u swiv.service -n 100 -f; \
	else echo "service not active, tailing ~/.cache/swiv.log"; tail -f $(HOME)/.cache/swiv.log; fi

test: venv
	$(PY) -m unittest discover -s tests -v

$(VENV)/.dev-installed: requirements-dev.txt $(VENV)/.installed
	$(VENV)/bin/pip install -q -r requirements-dev.txt
	touch $@

# both scanners always run; the target fails if either one reports a problem
security: $(VENV)/.dev-installed
	@rc=0; \
	$(VENV)/bin/bandit -r $(DIR) -x $(VENV),$(DIR)/tests,$(DIR)/.omc -ll || rc=1; \
	$(VENV)/bin/pip-audit -r requirements.txt --progress-spinner off || rc=1; \
	exit $$rc

uninstall:
	-systemctl --user disable --now swiv.service
	rm -f "$(UNIT)"
	systemctl --user daemon-reload
	@echo "config kept at $(CONFIG)"

clean:
	rm -rf $(VENV) __pycache__ tests/__pycache__

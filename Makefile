.PHONY: install setup run chat doctor backup update-skills start stop restart status logs help

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
BIN := $(VENV)/bin/python

help:
	@echo "GawdBotE — make targets:"
	@echo "  make setup          Full install: venv + service + 'gawdbote' command"
	@echo "  make install        Set up virtualenv and dependencies only"
	@echo "  make start          Start background service"
	@echo "  make stop           Stop background service"
	@echo "  make restart        Restart background service"
	@echo "  make status         Show service status"
	@echo "  make logs           Follow live logs"
	@echo "  make run            Run in foreground (dev mode)"
	@echo "  make chat           Interactive CLI chat"
	@echo "  make doctor         Run health checks"
	@echo "  make backup         Create a local backup"
	@echo "  make update-skills  Update all skills from clawhub"

setup:
	bash install.sh

install: $(VENV)/bin/activate

$(VENV)/bin/activate: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo ""
	@echo "Done! Copy .env.example to .env and fill in your API keys."
	@echo "Then: make run"

run: $(VENV)/bin/activate
	$(BIN) main.py

chat: $(VENV)/bin/activate
	$(BIN) main.py chat

doctor: $(VENV)/bin/activate
	$(BIN) main.py doctor

backup: $(VENV)/bin/activate
	$(BIN) main.py backup create

start:
	systemctl --user start gawdbote.service && echo "GawdBotE started."

stop:
	systemctl --user stop gawdbote.service && echo "GawdBotE stopped."

restart:
	systemctl --user restart gawdbote.service && echo "GawdBotE restarted."

status:
	systemctl --user status gawdbote.service --no-pager

logs:
	journalctl --user -u gawdbote.service -f --no-pager

update-skills:
	clawhub update --all --dir ./skills || echo "Install clawhub first: npm i -g clawhub"

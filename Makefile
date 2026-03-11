.PHONY: install run chat doctor backup update-skills help

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
BIN := $(VENV)/bin/python

help:
	@echo "GawdBotE — make targets:"
	@echo "  make install        Set up virtualenv and install dependencies"
	@echo "  make run            Start all interfaces (Telegram, Discord, Slack, webhooks, voice, cron)"
	@echo "  make chat           Interactive CLI chat"
	@echo "  make doctor         Run health checks"
	@echo "  make backup         Create a local backup"
	@echo "  make update-skills  Update all skills from clawhub"

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

update-skills:
	clawhub update --all --dir ./skills || echo "Install clawhub first: npm i -g clawhub"

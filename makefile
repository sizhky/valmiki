DATA_DIR?=data
DB_LOCAL?=$(DATA_DIR)/valmiki.db
DB_REMOTE=hetzner-first-machine:/var/lib/valmiki/valmiki.db

.PHONY: run test logs db-check db-pull db-push install-service

run:
	VALMIKI_DATA_DIR=$(DATA_DIR) uv run valmiki serve

test:
	uv run --extra dev pytest -q
	uv run ruff check src/valmiki tests

logs:
	tail -f $(DATA_DIR)/logs/valmiki.jsonl

db-check:
	sqlite3 $(DB_LOCAL) 'PRAGMA integrity_check;'

db-pull:
	scp $(DB_REMOTE) $(DB_LOCAL)

db-push:
	scp $(DB_LOCAL) $(DB_REMOTE)

install-service:
	install -m 0644 deploy/valmiki.service /etc/systemd/system/valmiki.service
	systemctl daemon-reload
	systemctl enable --now valmiki

DB_LOCAL=data/valmiki.db
DB_REMOTE=hetzner-first-machine:/root/valmiki/data/valmiki.db

.PHONY: run test logs db-check db-pull db-push

run:
	uv run valmiki serve

test:
	uv run --extra dev pytest -q
	uv run ruff check src/valmiki tests

logs:
	tail -f data/logs/valmiki.jsonl

db-check:
	sqlite3 $(DB_LOCAL) 'PRAGMA integrity_check;'

db-pull:
	scp $(DB_REMOTE) $(DB_LOCAL)

db-push:
	scp $(DB_LOCAL) $(DB_REMOTE)

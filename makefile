DB_LOCAL=data/valmiki.db
DB_REMOTE=hetzner-first-machine:/root/valmiki/data/valmiki.db

.PHONY: db-pull db-push

db-pull:
	scp $(DB_REMOTE) $(DB_LOCAL)

db-push:
	scp $(DB_LOCAL) $(DB_REMOTE)

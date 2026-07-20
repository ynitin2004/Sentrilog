.PHONY: up down reset ps logs psql

# Brings up the full local infra stack and blocks until every service with a
# healthcheck reports healthy (or fails fast if one doesn't).
up:
	docker compose up -d --wait

down:
	docker compose down

# Destroys volumes too -- full reset back to first-boot state (re-runs postgres-init).
reset:
	docker compose down -v

ps:
	docker compose ps

logs:
	docker compose logs -f

psql:
	docker compose exec postgres psql -U sentrilog -d sentrilog

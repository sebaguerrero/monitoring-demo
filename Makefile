.PHONY: poster-up poster-down poster-logs poster-status poster-fresh es-reset-logs fresh

poster-up:
	docker compose --env-file .env.poster --profile poster up -d --build

poster-down:
	docker compose --env-file .env.poster --profile poster down

poster-logs:
	docker compose --env-file .env.poster --profile poster logs -f

poster-status:
	docker compose --env-file .env.poster --profile poster ps

poster-fresh:
	docker compose --env-file .env.poster --profile poster down --rmi local --remove-orphans
	docker compose --env-file .env.poster --profile poster up -d --build

es-reset-logs:
	-curl -fsS -X DELETE 'http://localhost:9200/model-api-logs-*'
	@echo
	docker compose restart logstash filebeat

fresh:
	docker compose down -v --rmi local --remove-orphans
	docker compose up -d --build

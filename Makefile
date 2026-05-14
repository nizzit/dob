# ─────────────────────────────────────────────────────────────────────────────
# dob — MySQL dev helpers
# ─────────────────────────────────────────────────────────────────────────────

MYSQL_CONTAINER    := dob-mysql
MYSQL_IMAGE        := mysql:8.4
MYSQL_ROOT_PW      := secret
MYSQL_DB           := testshop

# Подключаться через toxiproxy (порт 3307 → MySQL:3306)
MYSQL_PORT         := 3307
MYSQL_DSN          := mysql://root:$(MYSQL_ROOT_PW)@127.0.0.1:$(MYSQL_PORT)/$(MYSQL_DB)

TOXI_API           := http://localhost:8474
TOXI_PROXY         := mysql

GENERATE_SCRIPT    := dob/tools/generate_testdb_mysql.py
INTERVAL           ?= 0.1
LATENCY            ?= 100

.PHONY: help up down logs mysql-logs mysql-shell \
        toxi-latency toxi-latency-off toxi-status \
        generate test \
        mysql-up mysql-down

help:
	@echo ""
	@echo "  up                — поднять MySQL + Toxiproxy (docker-compose)"
	@echo "  down              — остановить и удалить контейнеры"
	@echo "  logs              — логи всех контейнеров"
	@echo "  mysql-logs        — логи только MySQL"
	@echo "  mysql-shell       — mysql-клиент внутри контейнера"
	@echo ""
	@echo "  toxi-latency      — добавить задержку (LATENCY=мс, по умолч. 100)"
	@echo "  toxi-latency-off  — убрать задержку"
	@echo "  toxi-status       — показать активные токсины"
	@echo ""
	@echo "  generate          — наполнять БД тестовыми данными (Ctrl+C для остановки)"
	@echo "                      INTERVAL=<сек>  (по умолчанию 0.1)"
	@echo "  test              — запустить pytest"
	@echo ""
	@echo "  DSN: $(MYSQL_DSN)"
	@echo ""

# ── docker-compose ────────────────────────────────────────────────────────────

up:
	docker compose up -d
	@echo "Ожидаем готовности MySQL..."
	@for i in $$(seq 1 30); do \
		docker exec $(MYSQL_CONTAINER) mysqladmin ping -uroot -p$(MYSQL_ROOT_PW) --silent 2>/dev/null \
			&& echo "MySQL готов. Toxiproxy API: $(TOXI_API)" && break; \
		echo "  ...$$i/30"; \
		sleep 2; \
	done

down:
	docker compose down
	@echo "Контейнеры остановлены."

logs:
	docker compose logs -f

mysql-logs:
	docker logs -f $(MYSQL_CONTAINER)

mysql-shell:
	docker exec -it $(MYSQL_CONTAINER) mysql -uroot -p$(MYSQL_ROOT_PW) $(MYSQL_DB)

# ── toxiproxy ─────────────────────────────────────────────────────────────────

toxi-latency:
	@echo "Устанавливаем latency $(LATENCY)ms на прокси '$(TOXI_PROXY)'..."
	@if curl -sf $(TOXI_API)/proxies/$(TOXI_PROXY)/toxics/latency > /dev/null 2>&1; then \
		echo "  toxic уже существует — обновляем (PATCH)..."; \
		curl -sf -X PATCH $(TOXI_API)/proxies/$(TOXI_PROXY)/toxics/latency \
		  -H 'Content-Type: application/json' \
		  -d '{"attributes":{"latency":$(LATENCY),"jitter":0}}' \
		  | python3 -m json.tool; \
	 else \
		echo "  создаём новый toxic (POST)..."; \
		curl -sf -X POST $(TOXI_API)/proxies/$(TOXI_PROXY)/toxics \
		  -H 'Content-Type: application/json' \
		  -d '{"name":"latency","type":"latency","attributes":{"latency":$(LATENCY),"jitter":0}}' \
		  | python3 -m json.tool; \
	fi
	@echo ""

toxi-latency-off:
	@echo "Убираем latency с прокси '$(TOXI_PROXY)'..."
	curl -sf -X DELETE $(TOXI_API)/proxies/$(TOXI_PROXY)/toxics/latency
	@echo "Готово."

toxi-status:
	@echo "=== Proxies ==="
	@curl -sf $(TOXI_API)/proxies | python3 -m json.tool
	@echo ""
	@echo "=== Toxics on '$(TOXI_PROXY)' ==="
	@curl -sf $(TOXI_API)/proxies/$(TOXI_PROXY)/toxics | python3 -m json.tool

# ── data generation ───────────────────────────────────────────────────────────

generate:
	uv run python -u $(GENERATE_SCRIPT) $(MYSQL_DSN) --interval $(INTERVAL)

# ── tests ─────────────────────────────────────────────────────────────────────

test:
	uv run pytest

# ── обратная совместимость ────────────────────────────────────────────────────

mysql-up: up
mysql-down: down

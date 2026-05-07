# ─────────────────────────────────────────────────────────────────────────────
# dob — MySQL dev helpers
# ─────────────────────────────────────────────────────────────────────────────

MYSQL_CONTAINER  := dob-mysql
MYSQL_IMAGE      := mysql:8.4
MYSQL_PORT       := 3307
MYSQL_ROOT_PW    := secret
MYSQL_DB         := testshop
MYSQL_DSN        := mysql://root:$(MYSQL_ROOT_PW)@127.0.0.1:$(MYSQL_PORT)/$(MYSQL_DB)

GENERATE_SCRIPT  := dob/tools/generate_testdb_mysql.py
INTERVAL         ?= 1.5

.PHONY: help mysql-up mysql-down mysql-logs mysql-shell generate test

help:
	@echo ""
	@echo "  mysql-up      — запустить MySQL-контейнер ($(MYSQL_CONTAINER))"
	@echo "  mysql-down    — остановить и удалить контейнер"
	@echo "  mysql-logs    — показать логи контейнера"
	@echo "  mysql-shell   — открыть mysql-клиент внутри контейнера"
	@echo "  generate      — наполнять БД тестовыми данными (Ctrl+C для остановки)"
	@echo "                  INTERVAL=<сек>  — интервал между тиками (по умолчанию 1.5)"
	@echo "  test          — запустить pytest"
	@echo ""
	@echo "  DSN: $(MYSQL_DSN)"
	@echo ""

# ── MySQL container ───────────────────────────────────────────────────────────

mysql-up:
	@if docker ps -a --format '{{.Names}}' | grep -q '^$(MYSQL_CONTAINER)$$'; then \
		echo "Контейнер $(MYSQL_CONTAINER) уже существует, запускаем..."; \
		docker start $(MYSQL_CONTAINER); \
	else \
		echo "Создаём контейнер $(MYSQL_CONTAINER)..."; \
		docker run -d \
			--name $(MYSQL_CONTAINER) \
			-e MYSQL_ROOT_PASSWORD=$(MYSQL_ROOT_PW) \
			-e MYSQL_DATABASE=$(MYSQL_DB) \
			-p $(MYSQL_PORT):3306 \
			$(MYSQL_IMAGE); \
	fi
	@echo "Ожидаем готовности MySQL..."
	@for i in $$(seq 1 30); do \
		docker exec $(MYSQL_CONTAINER) mysqladmin ping -uroot -p$(MYSQL_ROOT_PW) --silent 2>/dev/null \
			&& echo "MySQL готов." && break; \
		echo "  ...$$i/30"; \
		sleep 2; \
	done

mysql-down:
	docker stop $(MYSQL_CONTAINER) 2>/dev/null || true
	docker rm   $(MYSQL_CONTAINER) 2>/dev/null || true
	@echo "Контейнер $(MYSQL_CONTAINER) удалён."

mysql-logs:
	docker logs -f $(MYSQL_CONTAINER)

mysql-shell:
	docker exec -it $(MYSQL_CONTAINER) mysql -uroot -p$(MYSQL_ROOT_PW) $(MYSQL_DB)

# ── data generation ───────────────────────────────────────────────────────────

generate:
	uv run python -u $(GENERATE_SCRIPT) $(MYSQL_DSN) --interval $(INTERVAL)

# ── tests ─────────────────────────────────────────────────────────────────────

test:
	uv run pytest

.PHONY: infra-up infra-down monitoring-up monitoring-down migrate api agent notification rag test lint frontend
infra-up:
	docker compose up -d
infra-down:
	docker compose down
monitoring-up:
	docker compose -f compose.local.monitoring.yaml up -d
monitoring-down:
	docker compose -f compose.local.monitoring.yaml down
migrate:
	uv run alembic -c backend/alembic.ini upgrade head
api:
	uv run oncall-api
agent:
	uv run oncall-agent-worker
rag:
	uv run oncall-rag-worker
notification:
	uv run oncall-notification-worker
test:
	uv run pytest -q
lint:
	uv run ruff check backend/src backend/tests
frontend:
	cd frontend && npm run dev

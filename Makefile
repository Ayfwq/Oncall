.PHONY: infra-up infra-down migrate api monitor agent rag test lint frontend
infra-up:
	docker compose up -d
infra-down:
	docker compose down
migrate:
	uv run alembic -c backend/alembic.ini upgrade head
api:
	uv run oncall-api
monitor:
	uv run oncall-monitor-worker
agent:
	uv run oncall-agent-worker
rag:
	uv run oncall-rag-worker
test:
	uv run pytest -q
lint:
	uv run ruff check backend/src backend/tests
frontend:
	cd frontend && npm run dev

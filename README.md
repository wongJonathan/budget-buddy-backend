# Starting:

```bash
docker compose up -d
```

Starts the docker container with the backend running.

Additional commands

```bash
docker compose exec app uv run alembic revision --autogenerate -m "message"
docker compose exec app uv run alembic upgrade head
docker compose restart app                 # picks up host code changes (no --reload in the container's uvicorn)
```
# Start the API (serves the dashboard at http://127.0.0.1:8000 when apps/web/dist is built).
Set-Location "$PSScriptRoot\..\services\backend"
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

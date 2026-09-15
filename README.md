# Agent Adapter Service

A minimal FastAPI service for adapting agent requests to backend providers.

## Requirements

- Python 3.11+

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The service is available at <http://127.0.0.1:8000>. API docs are at <http://127.0.0.1:8000/docs>.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Endpoints

- `GET /health` - service health check
- `POST /api/v1/adapter` - adapter placeholder; accepts `{ "input": "..." }`

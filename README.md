# Atelic × Dell × NVIDIA — Methane Incident Intelligence

Booth demo for **ADIPEC 2026**. Agentic AI that takes a methane leak from alarm to filed compliance record on a Dell edge box.

This repo is a **starter scaffold**. Nothing is wired yet — just the skeleton to begin work.

## Run

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: <http://localhost:8080>
- Backend:  <http://localhost:8000/health>
- Neo4j:    <http://localhost:7474>

## Layout

```
backend/   FastAPI service (Python 3.12, Dockerized)
frontend/  Static HTML + vanilla JS served by nginx
data/      Demo data (empty for now)
docs/      PRD and reference material
```

## Stack

- **Backend**: FastAPI + uvicorn
- **Frontend**: Plain HTML, Inter + JetBrains Mono, Atelic design tokens
- **Knowledge graph**: Neo4j 5.26-community + APOC, native `neo4j` Python driver

See `docs/atelic_dell_methane_ui_prd.md` for the full product spec.

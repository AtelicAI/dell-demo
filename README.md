# Atelic × Dell × NVIDIA — Methane Incident Intelligence

Booth demo for **ADIPEC 2026**. Agentic AI that takes a methane leak from alarm to filed compliance record on a Dell edge box.

The UI is connected to the live investigation engine: a camera alert from executed model inference on recorded
GasVid footage starts NVIDIA Nemotron agents that locate the source in a Neo4j knowledge graph, quantify the release
from InfluxDB telemetry, read 47 days of plant history, field reports and maintenance commitments, draft a cited work
order, answer the engineer's questions, revise on request, and verify the repair. Every decision stays with a person.

## Run locally

```bash
cp .env.example .env          # fill the secrets, or: deploy/gen_env.sh .env :8080 <demo-password>
docker compose up -d --build
docker compose --profile seed run --rm seed   # once per fresh volume set
```

Open <http://localhost:8080> (user `atelic`, the demo password). The UI calls the API at `/api` on the same origin.

## Deploy on AWS

```bash
aws cloudformation create-stack --stack-name atelic-dell-demo --template-body file://deploy/aws/stack.yaml \
  --parameters ParameterKey=AdminCidr,ParameterValue=<your-ip>/32
# key: aws ssm get-parameter --name /ec2/keypair/<KeyPairId> --with-decryption → .deploy/key.pem
NVIDIA_API_KEY=... deploy/gen_env.sh .deploy/aws.env <ip-dashed>.sslip.io <demo-password>
deploy/aws/deploy.sh --seed    # later updates: deploy/aws/deploy.sh
```

One Graviton host (t4g.large, eu-west-1) runs the whole stack in Docker Compose; Caddy serves HTTPS with a password.

## Layout

```
engine/    Investigation engine (FastAPI): agents, orchestrator, physics, Neo4j/InfluxDB/Qdrant/PostgreSQL, runtime data
backend/   UI API: translates engine records into the screens' shapes; holds the engineer credential server side
frontend/  Static HTML + vanilla JS, served by Caddy with the /api proxy
data/      UI-only assets (camera clips, Carbon Mapper context, camera wall)
deploy/    AWS CloudFormation stack, deploy script, secret generator
```

## What is real and what is not

- Footage: recorded METEC controlled release (GasVid MOV_1242). Model masks are executed OWLv2 + SAM2 output.
- Plant, telemetry, history, maintenance and field reports are synthetic; history wind and background come from measured METEC 2024 data.
  The plant releases the same metered rate as the footage, so the answer key (shown after the report) checks the estimate.
- Agents run on the NVIDIA hosted endpoint. Not running on the Dell edge server yet; the UI says so.
- See `engine/docs/STORY_RUNBOOK.md` for the demo script and `engine/docs/BACKEND_HANDOFF.md` for the engine API.

See `docs/atelic_dell_methane_ui_prd.md` for the full product spec.

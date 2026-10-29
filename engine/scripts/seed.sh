#!/bin/sh
# One-shot seed for a fresh deployment: workflow schema + engineer credential hash, least-privilege runtime role,
# knowledge graph, telemetry (scenarios + 47-day history) and document index. Idempotent; safe to re-run.
set -e
cd /srv
WORKFLOW_DATABASE_URL="$WORKFLOW_ADMIN_DATABASE_URL" python scripts/init_workflow.py
PROVISION_FROM_ENV=1 python scripts/provision_workflow_role.py
python scripts/seed_graph.py > /dev/null && echo "Knowledge graph seeded"
python scripts/seed_influx.py
python scripts/seed_documents.py
echo "Seed complete"

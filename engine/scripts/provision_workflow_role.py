"""Provision a non-owner runtime role; secrets stay in the local .env only."""
import os
import secrets
from pathlib import Path
import psycopg
from psycopg import sql

def provision_from_environment():
    """Container deployments: owner DSN and runtime password come from the environment; nothing is written."""
    with psycopg.connect(os.environ["WORKFLOW_ADMIN_DATABASE_URL"]) as conn:
        grant(conn, os.environ["WORKFLOW_RUNTIME_PASSWORD"])
    print("Provisioned least-privilege workflow runtime role from the environment")


def grant(conn, password):
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='atelic_runtime'").fetchone():
        conn.execute(sql.SQL("CREATE ROLE atelic_runtime LOGIN PASSWORD {}").format(sql.Literal(password)))
    else:
        conn.execute(sql.SQL("ALTER ROLE atelic_runtime PASSWORD {}").format(sql.Literal(password)))
    conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    conn.execute("GRANT CONNECT ON DATABASE atelic TO atelic_runtime")
    conn.execute("GRANT USAGE ON SCHEMA atelic TO atelic_runtime")
    conn.execute("GRANT SELECT,INSERT ON atelic.audit,atelic.drafts,atelic.reviews,atelic.reports,atelic.requests,atelic.incident_artifacts TO atelic_runtime")
    conn.execute("GRANT SELECT,INSERT,UPDATE ON atelic.incidents TO atelic_runtime")
    conn.execute("GRANT SELECT,INSERT,UPDATE ON atelic.draft_jobs TO atelic_runtime")
    conn.execute("GRANT SELECT,INSERT,UPDATE ON atelic.orchestration_runs TO atelic_runtime")
    conn.execute("GRANT SELECT,INSERT ON atelic.orchestration_events TO atelic_runtime")
    conn.execute("GRANT SELECT ON atelic.credentials TO atelic_runtime")


if __name__ == "__main__" and os.getenv("PROVISION_FROM_ENV") == "1":
    provision_from_environment()
elif __name__ == "__main__":
    env = Path(__file__).resolve().parents[1] / ".env"
    lines = env.read_text().splitlines()
    settings = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    owner_url = settings.get("WORKFLOW_ADMIN_DATABASE_URL", settings["WORKFLOW_DATABASE_URL"])
    password = settings.get("WORKFLOW_RUNTIME_PASSWORD", secrets.token_hex(24))
    with psycopg.connect(owner_url) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='atelic_runtime'").fetchone():
            conn.execute(sql.SQL("CREATE ROLE atelic_runtime LOGIN PASSWORD {}").format(sql.Literal(password)))
        else:
            conn.execute(sql.SQL("ALTER ROLE atelic_runtime PASSWORD {}").format(sql.Literal(password)))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute("GRANT CONNECT ON DATABASE atelic TO atelic_runtime")
        conn.execute("GRANT USAGE ON SCHEMA atelic TO atelic_runtime")
        conn.execute("GRANT SELECT,INSERT ON atelic.audit,atelic.drafts,atelic.reviews,atelic.reports,atelic.requests,atelic.incident_artifacts TO atelic_runtime")
        conn.execute("GRANT SELECT,INSERT,UPDATE ON atelic.incidents TO atelic_runtime")
        conn.execute("GRANT SELECT,INSERT,UPDATE ON atelic.draft_jobs TO atelic_runtime")
        conn.execute("GRANT SELECT,INSERT,UPDATE ON atelic.orchestration_runs TO atelic_runtime")
        conn.execute("GRANT SELECT,INSERT ON atelic.orchestration_events TO atelic_runtime")
        conn.execute("GRANT SELECT ON atelic.credentials TO atelic_runtime")
    updates = dict(WORKFLOW_ADMIN_DATABASE_URL=owner_url, WORKFLOW_RUNTIME_PASSWORD=password,
                   WORKFLOW_DATABASE_URL="postgresql://atelic_runtime:"+password+"@127.0.0.1:5546/atelic")
    output = []
    for line in lines:
        key = line.split("=", 1)[0]
        if key in updates:
            output.append(key+"="+updates.pop(key))
        else:
            output.append(line)
    output += [k+"="+v for k,v in updates.items()]
    env.write_text("\n".join(output)+"\n")
    env.chmod(0o600)
    print("Provisioned least-privilege workflow runtime role; owner migration DSN retained separately")

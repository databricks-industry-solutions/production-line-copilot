"""Initialize Lakebase schema and tables.

This script is intended to be used in a Solution Accelerator-style install flow.
It avoids hard-coding workspace-specific connection details.

Auth:
- Preferred: provide a password/token via env var `PGPASSWORD` (or `LAKEBASE_OAUTH_TOKEN`)
- Fallback: pass the token as the first CLI argument

Connection info:
- Uses standard Postgres env vars: PGHOST, PGPORT, PGUSER, PGDATABASE, PGSSLMODE
"""

from __future__ import annotations

import os
import sys

import psycopg2


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise ValueError(f"Missing required env var: {name}")
    return v


def main() -> None:
    host = _env("PGHOST")
    port = int(os.environ.get("PGPORT", "5432"))
    user = _env("PGUSER")
    db = _env("PGDATABASE")
    sslmode = os.environ.get("PGSSLMODE", "require")

    token = os.environ.get("PGPASSWORD") or os.environ.get("LAKEBASE_OAUTH_TOKEN") or ""
    if not token and len(sys.argv) > 1:
        token = sys.argv[1]
    if not token:
        raise ValueError(
            "Missing Lakebase password/token. Provide PGPASSWORD (preferred) or LAKEBASE_OAUTH_TOKEN, "
            "or pass it as the first CLI argument."
        )

    conn = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=token,
        dbname=db,
        sslmode=sslmode,
        connect_timeout=10,
    )
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute("SELECT version()")
        print("Connected:", cur.fetchone()[0][:60])

        cur.execute("CREATE SCHEMA IF NOT EXISTS ai_chatbot")

        cur.execute(
            """
CREATE TABLE IF NOT EXISTS ai_chatbot.conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shift_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
)
""".strip()
        )

        cur.execute(
            """
CREATE TABLE IF NOT EXISTS ai_chatbot.messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES ai_chatbot.conversations(conversation_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
)
""".strip()
        )

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON ai_chatbot.messages(conversation_id)"
        )

        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'ai_chatbot' ORDER BY table_name"
        )
        tables = [r[0] for r in cur.fetchall()]
        print("Tables:", tables)
    finally:
        cur.close()
        conn.close()

    print("Done!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise

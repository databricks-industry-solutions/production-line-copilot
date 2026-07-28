"""Force app-SP identity for ``WorkspaceClient`` calls that hit app-owned resources.

Why this exists
---------------
The app declares user_authorization scopes (``iam.current-user:read``,
``iam.access-control:read``) so the UI can show the signed-in user's email.
Any user_authorization scope, even read-only ones, causes the Databricks
Apps runtime to inject an ``X-Forwarded-Access-Token`` header carrying the
browsing user's OBO token. The default SDK auth chain can pick that token
up when ``WorkspaceClient()`` is called inside a request handler.

That's fatal for backend queries against **app-owned resources** (SQL
warehouse ``CAN_USE``, ``system.billing.usage`` ``SELECT``, jobs
``CAN_MANAGE_RUN``, Lakebase role, Genie space): the grants live on the
app SP, not on every user, so the query fails with

    PERMISSION_DENIED: <user-obo-sp-uuid> is not authorized to use this
    SQL Endpoint

Every code path that hits an app-owned resource must build its
``WorkspaceClient`` via :func:`app_workspace_client` so authentication is
pinned to the app SP creds the Apps runtime injects as
``DATABRICKS_CLIENT_ID`` / ``DATABRICKS_CLIENT_SECRET``. The user-token
path is deliberately not reachable from here — callers that genuinely need
OBO (rare) build a ``WorkspaceClient`` themselves.

Local dev
---------
When the SP env vars aren't set (local dev with a PAT / user OAuth
profile), the helper falls back to the default auth chain, matching the
previous behaviour.
"""
from __future__ import annotations

import logging
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config

logger = logging.getLogger(__name__)


def app_workspace_client() -> WorkspaceClient:
    """Return a ``WorkspaceClient`` pinned to the app SP when possible.

    Uses OAuth-M2M with ``DATABRICKS_CLIENT_ID`` + ``DATABRICKS_CLIENT_SECRET``
    when both are set (the Apps runtime always sets these). Falls back to
    the SDK default auth chain otherwise (local dev with PAT / user OAuth).
    """
    client_id = (os.environ.get("DATABRICKS_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("DATABRICKS_CLIENT_SECRET") or "").strip()
    host = (os.environ.get("DATABRICKS_HOST") or "").strip()

    if client_id and client_secret and host:
        return WorkspaceClient(config=Config(
            host=host,
            client_id=client_id,
            client_secret=client_secret,
            auth_type="oauth-m2m",
        ))

    logger.debug(
        "app_workspace_client: SP creds not set, falling back to default auth chain "
        "(client_id_set=%s, client_secret_set=%s, host_set=%s)",
        bool(client_id), bool(client_secret), bool(host),
    )
    return WorkspaceClient()

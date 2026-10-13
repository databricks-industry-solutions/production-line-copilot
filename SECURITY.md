# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
privately, **do not open a public GitHub issue** for security problems.

- Email **bugbounty@databricks.com** with the details.
- Include a description of the issue, the affected files or components, and, if
  possible, steps to reproduce or a proof of concept.
- Please give us a reasonable amount of time to investigate and address the
  issue before any public disclosure.

We will acknowledge receipt of your report and keep you informed of the
progress toward a fix.

## Scope

This repository is a **solution accelerator** intended as a starting point for
your own deployment. It ships with placeholder configuration and synthetic data
(see `NOTICE`). Before deploying to any real environment:

- Fill in `config/project-settings.local.yaml` with your own workspace values;
  never commit real hosts, warehouse IDs, service-principal IDs, or secrets.
- Store credentials in Databricks secret scopes, not in source.
- Review and scope down the Unity Catalog, Lakebase, and app service-principal
  grants created during deployment to fit your security requirements.

## Known advisories in dependencies

Checked against GitHub Dependabot and [OSV](https://osv.dev), across both locked
trees. Transitive Python floors live in `[tool.uv] constraint-dependencies`;
transitive npm floors live in `overrides` in `package.json`.

The following remain, all pulled in transitively and all held below their fix by
an upstream pin, so they cannot be resolved from this repository:

| Package | Locked | Advisory | Fixed in | Held back by |
|---|---|---|---|---|
| `cryptography` | 49.0.0 | GHSA-g6cj-pr64-35w5 (high) | 50.0.0 | `mlflow` requires `cryptography<50` |
| `protobuf` | 6.31.1 (Python &lt; 3.12) | GHSA-7gcm-g887-7qv7 (high) | 6.33.5 | `ortools` requires `protobuf<6.32` |
| `oauthlib` | 3.3.1 | GHSA-xpv3-w29h-x7cv / GHSA-hj66-6f7g-4r5v (medium) | 4.0.0 | `databricks-sql-connector` requires `oauthlib<4` |
| `thrift` | 0.20.0 | GHSA for compressed-data / infinite-loop / host mismatch (high/medium) | 0.24.0 | `databricks-sql-connector` requires `thrift<0.21` |

The accelerator does not exercise the affected code paths — it does no PKCS#7
decryption, parses no untrusted protobuf JSON, does not host an OAuth
authorization server, and does not accept untrusted Thrift payloads — so the
practical exposure here is low. That is a statement about this application, not
about the advisories: if you build on this and add any of those, re-run the
check. They resolve on their own once mlflow, ortools, and
databricks-sql-connector relax their pins, so re-running `uv lock --upgrade`
periodically is worthwhile.

To reproduce the check:

```bash
pip-audit          # or: npm audit
```

## Supported Versions

This project is provided as-is under the Apache-2.0 License. Fixes are applied
to the `main` branch; there is no separate long-term-support branch.

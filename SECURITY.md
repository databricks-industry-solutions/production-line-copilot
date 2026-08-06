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

Checked at the v0.3.6 release against [OSV](https://osv.dev): all 938 packages
in the npm tree are clean. No dependency changed in v0.3.7 — neither `uv.lock`
nor `package-lock.json` was touched — so the review below is still current. Two
Python packages carry advisories, both pulled in transitively and both held
below their fix by an upstream pin, so neither can be resolved from this
repository:

| Package | Version | Advisories | Fixed in | Held back by |
|---|---|---|---|---|
| `cryptography` | 48.0.1 | GHSA-g6cj-pr64-35w5, GHSA-jwv3-5hgf-82ww (high), GHSA-m2h6-j472-rp4c (moderate) | 49.0.0 | `mlflow` requires `cryptography<49` |
| `protobuf` | 6.31.1 | GHSA-7gcm-g887-7qv7 (high) | 6.33.5 | `ortools` requires `protobuf<6.32` |

The accelerator does not exercise the affected code paths — it does no PKCS#7
decryption, builds no X.509 chains of its own, and parses no untrusted protobuf
JSON — so the practical exposure here is low. That is a statement about this
application, not about the advisories: if you build on this and add any of
those, re-run the check. Both resolve on their own once mlflow and ortools relax
their pins, so re-running `uv lock --upgrade` periodically is worthwhile.

To reproduce the check:

```bash
pip-audit          # or: npm audit
```

## Supported Versions

This project is provided as-is under the Apache-2.0 License. Fixes are applied
to the `main` branch; there is no separate long-term-support branch.

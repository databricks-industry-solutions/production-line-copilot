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

Checked at the v0.4.0 release against [OSV](https://osv.dev), across both
locked trees. Three npm packages had moved into an advisory since v0.3.9 and
each had a fix available, so each is pinned forward through `overrides` in
`package.json`: `js-yaml` 4.3.1 (quadratic CPU on `!!omap`), `dompurify` 3.4.13
(a detached subtree left executable by `IN_PLACE` hook removal), and `nanoid`
3.3.18 (an infinite loop on a zero size, fixed in 3.3.17). All three reach us transitively; with
those overrides the npm tree is clean.

On the Python side `cryptography` moved 48.0.1 → 49.0.0, which closes an
exponential path-building denial of service and a wildcard-DNS escape from
`permittedSubtrees`. That upgrade only became reachable because `mlflow` relaxed
its pin in 3.15.1, which this release also takes.

Two advisories remain, both pulled in transitively and both held below their fix
by an upstream pin, so neither can be resolved from this repository:

| Package | Version | Advisory | Fixed in | Held back by |
|---|---|---|---|---|
| `cryptography` | 49.0.0 | GHSA-g6cj-pr64-35w5 (high) | 50.0.0 | `mlflow` requires `cryptography<50` |
| `protobuf` | 6.31.1 | GHSA-7gcm-g887-7qv7 (high) | 6.33.5 | `ortools` requires `protobuf<6.32` |

The accelerator does not exercise either affected code path — it does no PKCS#7
decryption and parses no untrusted protobuf JSON — so the practical exposure
here is low. That is a statement about this
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

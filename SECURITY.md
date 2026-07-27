# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
privately — **do not open a public GitHub issue** for security problems.

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

## Supported Versions

This project is provided as-is under the Apache-2.0 License. Fixes are applied
to the `main` branch; there is no separate long-term-support branch.

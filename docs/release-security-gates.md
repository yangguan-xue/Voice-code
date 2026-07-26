# Release Security Gates

Round 9 CI blocks release and merge candidates with these gates:

- `secret-scan` runs `detect-secrets` across tracked files and fails on unaudited findings.
- `secret-scan` also checks literal `api_key`, `Authorization`, and `token` assignments outside lockfiles and examples.
- `license-scan` runs `pip-licenses` and fails on GPL, AGPL, LGPL, SSPL, or BUSL licenses.
- `build` generates `sbom.cdx.json` with `cyclonedx-py` and uploads it with release artifacts.

The gates must not print credential values in logs. Findings should identify file and line only, then remediation should happen in a follow-up commit that removes or rotates the credential.

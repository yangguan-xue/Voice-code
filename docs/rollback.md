# Rollback

Use this runbook when a published `voice-code` wheel fails smoke, migration, or clean-environment validation after release.

## Inputs

- Previous stable version: `X.Y.Z`
- Release commit SHA and CI run URL
- Downloaded `checksum.txt` from the failed release
- Failed wheel filename from the release artifact bundle

## Install Previous Stable Version

```bash
python -m pip install --upgrade voice-code==X.Y.Z
reasoning --version
reasoning --plain --help
```

## Verify Artifact Integrity Before Reuse

```bash
shasum -a 256 -c checksum.txt
```

If signature files are present, verify them before promoting or reinstalling any artifact:

```bash
python -m pip install sigstore
sigstore verify identity --cert-identity "$GITHUB_ACTOR" --cert-oidc-issuer https://token.actions.githubusercontent.com dist/*.whl
```

## Rollback Steps

1. Freeze writes or stop workers that could run new migrations.
2. Install the previous stable package with `pip install voice-code==X.Y.Z`.
3. Run `reasoning --plain --help` and `reasoning --version` on a clean host.
4. Restore SQLite from the latest verified backup only if the new release already changed local state.
5. Rebuild optional Milvus indexes from SQLite authority data after the older binary is healthy.
6. Record the previous version, checksum result, CI run, and smoke output in the release incident notes.

## Exit Criteria

- `reasoning --plain --help` exits successfully.
- `reasoning --version` prints the previous stable version.
- `checksum.txt` verification succeeds for any reused artifact.
- SQLite integrity check returns `ok` before workers are resumed.

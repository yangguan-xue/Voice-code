# Build Provenance

Round 9 release artifacts are produced only by CI after lint, type, test, coverage, secret, and license gates complete.

The build job records:

- Source commit from `github.sha`
- Dependency resolution from `uv.lock`
- Wheel and source distributions in `dist/`
- SHA-256 digests in `checksum.txt`
- CycloneDX SBOM in `sbom.cdx.json`
- Sigstore wheel signatures on non-PR release builds

The smoke job downloads the release artifact bundle, runs the SQLite migration smoke script, runs optional Milvus health only when `MILVUS_URI` is present, and verifies `uv run reasoning --plain --help` in the clean CI environment.

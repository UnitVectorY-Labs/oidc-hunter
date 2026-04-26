[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT) [![Work In Progress](https://img.shields.io/badge/Status-Work%20In%20Progress-yellow)](https://guide.unitvectorylabs.com/bestpractices/status/#work-in-progress) 

# oidc-hunter

An autonomous agent that discovers public OpenID Connect endpoints, verifies them, and maintains a reviewable candidate set for catalog inclusion.

## Running

The primary entrypoint is [`run.sh`](/Users/jaredhatfield/github/oidc-hunter/run.sh). It:

- prefers the macOS `container` runtime when available
- falls back to `docker`
- then falls back to `podman`
- mounts [`data/`](/Users/jaredhatfield/github/oidc-hunter/data) into the container as `/data`
- writes the SQLite database, `candidates.yaml`, reports, lessons, and run artifacts into that mounted directory

The script also normalizes the repo's current `.env` variable names into the `OIDC_HUNTER_*` variables the app expects.

```bash
./run.sh
```

For a smaller live verification run, override the budgets:

```bash
OIDC_HUNTER_INVESTIGATION_ITERATIONS=1 \
OIDC_HUNTER_REVIEW_ITERATIONS=1 \
OIDC_HUNTER_CLOUDFLARE_TOP_LIMIT=10 \
OIDC_HUNTER_CLOUDFLARE_SEED_SAMPLE_SIZE=5 \
./run.sh
```

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${ROOT_DIR}/data"
IMAGE_TAG="${OIDC_HUNTER_IMAGE_TAG:-oidc-hunter:dev}"

mkdir -p "${DATA_DIR}"

if [[ "${OIDC_HUNTER_SKIP_DOTENV:-0}" != "1" && -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

if [[ -n "${OPENAPI_BASE_URL:-}" && -z "${OIDC_HUNTER_LLM_BASE_URL:-}" ]]; then
  export OIDC_HUNTER_LLM_BASE_URL="${OPENAPI_BASE_URL}"
fi
if [[ -n "${OPENAI_BASE_URL:-}" && -z "${OIDC_HUNTER_LLM_BASE_URL:-}" ]]; then
  export OIDC_HUNTER_LLM_BASE_URL="${OPENAI_BASE_URL}"
fi
if [[ -n "${MODEL:-}" && -z "${OIDC_HUNTER_LLM_MODEL:-}" ]]; then
  export OIDC_HUNTER_LLM_MODEL="${MODEL}"
fi
if [[ -n "${CLOUDFLARE_API_KEY:-}" && -z "${OIDC_HUNTER_CLOUDFLARE_API_TOKEN:-}" ]]; then
  export OIDC_HUNTER_CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_KEY}"
fi

export OIDC_HUNTER_STATE_DIR="/data"

choose_runtime() {
  if [[ "$(uname -s)" == "Darwin" ]] && command -v container >/dev/null 2>&1; then
    printf '%s\n' "container"
    return 0
  fi
  if command -v docker >/dev/null 2>&1; then
    printf '%s\n' "docker"
    return 0
  fi
  if command -v podman >/dev/null 2>&1; then
    printf '%s\n' "podman"
    return 0
  fi
  return 1
}

RUNTIME="$(choose_runtime || true)"
if [[ -z "${RUNTIME}" ]]; then
  printf '%s\n' "No supported container runtime found. Expected: container, docker, or podman." >&2
  exit 1
fi

declare -a BUILD_CMD=("${RUNTIME}" build -t "${IMAGE_TAG}" "${ROOT_DIR}")
declare -a RUN_CMD=(
  "${RUNTIME}" run --rm
  --volume "${DATA_DIR}:/data"
  --env "OIDC_HUNTER_STATE_DIR=/data"
)

for env_name in \
  OIDC_HUNTER_LLM_BASE_URL \
  OIDC_HUNTER_LLM_MODEL \
  OIDC_HUNTER_LLM_API_KEY \
  OIDC_HUNTER_LLM_TIMEOUT_SECONDS \
  OIDC_HUNTER_AGENTIC_TIMEOUT_SECONDS \
  OIDC_HUNTER_CATALOG_URL \
  OIDC_HUNTER_CLOUDFLARE_API_TOKEN \
  OIDC_HUNTER_CLOUDFLARE_BASE_URL \
  OIDC_HUNTER_CLOUDFLARE_DATASET_ALIAS \
  OIDC_HUNTER_CLOUDFLARE_TOP_LIMIT \
  OIDC_HUNTER_CLOUDFLARE_SEED_SAMPLE_SIZE \
  OIDC_HUNTER_PROBE_DOMAINS \
  OIDC_HUNTER_PROBE_TIMEOUT_SECONDS \
  OIDC_HUNTER_PROBE_CONCURRENCY \
  OIDC_HUNTER_INVESTIGATION_ITERATIONS \
  OIDC_HUNTER_REVIEW_ITERATIONS \
  OIDC_HUNTER_INVESTIGATION_TARGET_LIMIT \
  OIDC_HUNTER_KEEP_PROBE_ARTIFACTS
do
  if [[ -n "${!env_name:-}" ]]; then
    RUN_CMD+=(--env "${env_name}=${!env_name}")
  fi
done

RUN_CMD+=("${IMAGE_TAG}")

printf 'Using runtime: %s\n' "${RUNTIME}"
printf 'Building image: %s\n' "${IMAGE_TAG}"
"${BUILD_CMD[@]}"

printf 'Running with persistent state dir: %s\n' "${DATA_DIR}"
"${RUN_CMD[@]}"

#!/bin/bash
# The arbitrary UID OpenShift assigns has no /etc/passwd entry. Rather than
# write one here (which would require a group-writable /etc/passwd), the image
# sets USER/LOGNAME so getpass.getuser() resolves from the environment.

ENV_ARGS=()
if [[ -n "${SHARED_ENV}" ]]; then
  ENV_ARGS+=("--env-file=${SHARED_ENV}")
fi

if [[ "${ENABLE_RAY_SERVE}" == "true" ]]; then
  echo "🔁 Starting with Ray Serve..."
  uv run "${ENV_ARGS[@]}" api.py
else
  echo "🚀 Starting with Uvicorn..."
  # --reload is dev-only (set UVICORN_RELOAD=true) and needs a single worker.
  RELOAD_ARGS=()
  WORKERS="${API_NUM_WORKERS:-1}"
  if [[ "${UVICORN_RELOAD}" == "true" ]]; then
    RELOAD_ARGS+=("--reload")
    WORKERS="1"
  fi
  uv run --no-dev "${ENV_ARGS[@]}" uvicorn api:app --host 0.0.0.0 --port "${APP_iPORT:-8080}" "${RELOAD_ARGS[@]}" --workers "${WORKERS}"
fi

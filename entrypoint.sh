#!/bin/bash
# OpenShift (and any runtime that injects an arbitrary UID) starts this
# container with a UID that has no /etc/passwd entry. Dependencies that call
# pwd.getpwuid()/getpass.getuser() (Ray, uv, Hugging Face) crash without one,
# so add an entry on the fly. /etc/passwd is group-writable (GID 0) for exactly
# this, per the OpenShift container guidelines. Use a UID-specific name so we
# never collide with the image's baked-in `openrag` user (a different UID).
if ! whoami >/dev/null 2>&1 && [[ -w /etc/passwd ]]; then
  uid="$(id -u)"
  printf 'openrag-%s:x:%s:0:OpenRAG:/app/home:/sbin/nologin\n' "$uid" "$uid" >> /etc/passwd
fi

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

#!/usr/bin/env bash
# Default port-forward for Capiba services.
# Usage: ./scripts/port-forward.sh [start|stop|status]
# Runs in the background and redirects logs to /tmp/capiba-port-forward-*.log

set -euo pipefail

NAMESPACE="${NAMESPACE:-capiba}"
# Format: name:local_port:remote_port[:namespace[:service]]
# Defaults: namespace=$NAMESPACE, service=capiba-<name>
SERVICES=(
  "api:8000:8000"
  "grafana:3000:3000"
  "arangodb:8529:8529"
  "minio:9001:9001"
  "minio:9000:9000"
  "marquez:3001:3000"
  "trino:8081:8080"
  "iceberg-catalog:8181:8181"
  "airflow:8080:8080"
  "keycloak:8182:8080"
  "headlamp:4466:80:headlamp:headlamp"
)

# Parses an entry into the globals PF_SVC, PF_LOCAL, PF_REMOTE, PF_NS, PF_MATCH.
parse_entry() {
  local entry="$1"
  IFS=':' read -r name PF_LOCAL PF_REMOTE PF_NS PF_SVC <<< "$entry"
  PF_NS="${PF_NS:-$NAMESPACE}"
  PF_SVC="${PF_SVC:-capiba-${name}}"
  PF_MATCH="port-forward svc/${PF_SVC} ${PF_LOCAL}:${PF_REMOTE}.*-n ${PF_NS}"
}

start() {
  echo "Starting port-forwards for namespace ${NAMESPACE}..."
  for svc in "${SERVICES[@]}"; do
    parse_entry "$svc"
    log_file="/tmp/capiba-port-forward-${name}-${PF_LOCAL}.log"

    if pgrep -f "${PF_MATCH}" > /dev/null; then
      echo "  ${name} is already on ${PF_LOCAL}"
      continue
    fi

    nohup kubectl port-forward "svc/${PF_SVC}" "${PF_LOCAL}:${PF_REMOTE}" \
      -n "${PF_NS}" > "${log_file}" 2>&1 &
    echo "  ${name} → http://localhost:${PF_LOCAL} (log: ${log_file})"
  done
}

stop() {
  echo "Stopping Capiba port-forwards..."
  for svc in "${SERVICES[@]}"; do
    parse_entry "$svc"
    pkill -f "${PF_MATCH}" || true
  done
  echo "Done."
}

status() {
  echo "Active port-forwards:"
  for svc in "${SERVICES[@]}"; do
    parse_entry "$svc"
    if pgrep -f "${PF_MATCH}" > /dev/null; then
      echo "  ${name}: http://localhost:${PF_LOCAL} (active)"
    else
      echo "  ${name}: http://localhost:${PF_LOCAL} (inactive)"
    fi
  done
}

case "${1:-start}" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  status)
    status
    ;;
  *)
    echo "Usage: $0 [start|stop|status]"
    exit 1
    ;;
esac

#!/usr/bin/env bash
# Generates a self-signed wildcard TLS certificate for *.capiba.local and
# stores it as the capiba-tls secret (used by the chart's Ingress tls block).
# Idempotent: skips generation when the secret already exists.
#
# Self-signed: browsers show a warning on first access (accept the exception,
# or replace with a mkcert/CA-signed cert in real deployments).
#
# Usage: ./scripts/gen-certs.sh [namespace]

set -euo pipefail

NAMESPACE="${1:-capiba}"
SECRET_NAME="capiba-tls"
DOMAIN="capiba.local"

if kubectl get secret "${SECRET_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
  echo "Secret ${SECRET_NAME} already exists in namespace ${NAMESPACE}, skipping."
  exit 0
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "${WORKDIR}/tls.key" \
  -out "${WORKDIR}/tls.crt" \
  -days 825 \
  -subj "/CN=*.${DOMAIN}" \
  -addext "subjectAltName=DNS:*.${DOMAIN},DNS:${DOMAIN}" \
  -addext "basicConstraints=CA:FALSE" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" \
  >/dev/null 2>&1

kubectl create secret tls "${SECRET_NAME}" \
  --namespace "${NAMESPACE}" \
  --cert="${WORKDIR}/tls.crt" \
  --key="${WORKDIR}/tls.key"

echo "TLS secret ${SECRET_NAME} created (*.${DOMAIN}, valid for 825 days)."

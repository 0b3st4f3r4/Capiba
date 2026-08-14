#!/usr/bin/env bash
# Upgrades the Helm chart using values from the local .env.
# Avoids leaving credentials and paths hardcoded in values.yaml.
#
# Usage: ./scripts/helm-upgrade.sh [extra helm args]

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

# Reads only the required variables from .env, ignoring comments and
# blank lines. Values with spaces/wildcards do not break the script.
_load_env_var() {
  local var_name="$1"
  local default_value="${2:-}"
  if [[ -f "${ENV_FILE}" ]]; then
    local value
    value=$(grep -E "^${var_name}=" "${ENV_FILE}" | tail -n 1 | cut -d '=' -f 2-)
    printf '%s' "${value:-${default_value}}"
  else
    printf '%s' "${default_value}"
  fi
}

DATA_PATH="$(_load_env_var DATA_PATH "${PROJECT_ROOT}/data")"
SERVICES_DATA_PATH="${PROJECT_ROOT}/services"
TRANSPARENCY_API_KEY="$(_load_env_var TRANSPARENCY_API_KEY "")"

# Ingress domain shared by all services.
INGRESS_DOMAIN="$(_load_env_var INGRESS_DOMAIN "capiba.local")"

# PostgreSQL credentials.
POSTGRESQL_PASSWORD="$(_load_env_var POSTGRESQL_PASSWORD "capiba-secret")"
POSTGRESQL_DWH_PASSWORD="$(_load_env_var POSTGRESQL_DWH_PASSWORD "dwh-secret")"

# ArangoDB credentials.
ARANGODB_ROOT_PASSWORD="$(_load_env_var ARANGODB_ROOT_PASSWORD "capiba-arangodb")"

# MinIO credentials.
MINIO_ROOT_USER="$(_load_env_var MINIO_ROOT_USER "capiba")"
MINIO_ROOT_PASSWORD="$(_load_env_var MINIO_ROOT_PASSWORD "capiba-secret")"
MINIO_TRINO_SECRET_KEY="$(_load_env_var MINIO_TRINO_SECRET_KEY "trino-secret")"
MINIO_AIRFLOW_LOGS_SECRET_KEY="$(_load_env_var MINIO_AIRFLOW_LOGS_SECRET_KEY "airflow-logs-secret")"

# Iceberg catalog (Lakekeeper) credentials.
ICEBERG_CATALOG_DATABASE_PASSWORD="$(_load_env_var ICEBERG_CATALOG_DATABASE_PASSWORD "lakekeeper-secret")"
ICEBERG_CATALOG_PG_ENCRYPTION_KEY="$(_load_env_var ICEBERG_CATALOG_PG_ENCRYPTION_KEY "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")"

# Trino credentials.
TRINO_OAUTH2_CLIENT_SECRET="$(_load_env_var TRINO_OAUTH2_CLIENT_SECRET "trino-oidc-secret")"
TRINO_INTERNAL_SHARED_SECRET="$(_load_env_var TRINO_INTERNAL_SHARED_SECRET "trino-internal-shared-secret")"
TRINO_INTERNAL_PASSWORD="$(_load_env_var TRINO_INTERNAL_PASSWORD "capiba-trino")"

# Grafana fallback admin.
GRAFANA_ADMIN_PASSWORD="$(_load_env_var GRAFANA_ADMIN_PASSWORD "capiba-grafana")"

# Airflow secrets.
AIRFLOW_JWT_SECRET="$(_load_env_var AIRFLOW_JWT_SECRET "MiuT43RXgkfSRv7X4923vP73byiiLh2hkDSxTmetErI=")"
AIRFLOW_FERNET_KEY="$(_load_env_var AIRFLOW_FERNET_KEY "fPoiu0eoT46TSxhd4WYPPampDl6YNm8qLr6KFD9jyZm=")"
AIRFLOW_DATABASE_PASSWORD="$(_load_env_var AIRFLOW_DATABASE_PASSWORD "airflow-secret")"

# Marquez database password.
MARQUEZ_DATABASE_PASSWORD="$(_load_env_var MARQUEZ_DATABASE_PASSWORD "marquez-secret")"

# Capiba API / portal session secret.
API_PORTAL_SESSION_SECRET="$(_load_env_var API_PORTAL_SESSION_SECRET "capiba-portal-session-secret")"

# Keycloak master admin and database credentials.
KEYCLOAK_ADMIN_USERNAME="$(_load_env_var KEYCLOAK_ADMIN_USERNAME "admin")"
KEYCLOAK_ADMIN_PASSWORD="$(_load_env_var KEYCLOAK_ADMIN_PASSWORD "keycloak-admin-secret")"
KEYCLOAK_DATABASE_PASSWORD="$(_load_env_var KEYCLOAK_DATABASE_PASSWORD "keycloak-secret")"

# Keycloak OIDC client secrets for the capiba realm.
KEYCLOAK_CLIENT_SECRET_CAPIBA_DASHBOARD="$(_load_env_var KEYCLOAK_CLIENT_SECRET_CAPIBA_DASHBOARD "capiba-dashboard-secret")"
KEYCLOAK_CLIENT_SECRET_MINIO="$(_load_env_var KEYCLOAK_CLIENT_SECRET_MINIO "minio-oidc-secret")"
KEYCLOAK_CLIENT_SECRET_GRAFANA="$(_load_env_var KEYCLOAK_CLIENT_SECRET_GRAFANA "grafana-oidc-secret")"
KEYCLOAK_CLIENT_SECRET_AIRFLOW="$(_load_env_var KEYCLOAK_CLIENT_SECRET_AIRFLOW "airflow-oidc-secret")"
KEYCLOAK_CLIENT_SECRET_CAPIBA_SERVICES="$(_load_env_var KEYCLOAK_CLIENT_SECRET_CAPIBA_SERVICES "capiba-services-secret")"

# Keycloak dev user (realm capiba). Applied on every upgrade by the
# post-install/upgrade hook job keycloak/job-sync-user.yaml (--import-realm
# only creates the realm on first boot and skips it afterwards).
KEYCLOAK_DEV_USERNAME="$(_load_env_var KEYCLOAK_DEV_USERNAME "capiba")"
KEYCLOAK_DEV_PASSWORD="$(_load_env_var KEYCLOAK_DEV_PASSWORD "capiba-sso")"
KEYCLOAK_DEV_EMAIL="$(_load_env_var KEYCLOAK_DEV_EMAIL "capiba@capiba.local")"

RELEASE="${RELEASE:-capiba}"
NAMESPACE="${NAMESPACE:-capiba}"

EXTRA_ARGS=("$@")

echo "Checking Helm release: release=${RELEASE}, namespace=${NAMESPACE}"

HELM_CMD="upgrade"
if ! helm status "${RELEASE}" --namespace "${NAMESPACE}" >/dev/null 2>&1; then
  echo "Release not found; running install."
  HELM_CMD="install"
else
  echo "Existing release; running upgrade."
fi

helm "${HELM_CMD}" "${RELEASE}" "${PROJECT_ROOT}/charts/capiba" \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --set airflow.enabled=true \
  --set global.dataPath="${DATA_PATH}" \
  --set global.servicesDataPath="${SERVICES_DATA_PATH}" \
  --set global.transparencyApiKey="${TRANSPARENCY_API_KEY}" \
  --set ingress.domain="${INGRESS_DOMAIN}" \
  --set postgresql.auth.password="${POSTGRESQL_PASSWORD}" \
  --set postgresql.dwh.password="${POSTGRESQL_DWH_PASSWORD}" \
  --set arangodb.auth.rootPassword="${ARANGODB_ROOT_PASSWORD}" \
  --set minio.auth.rootUser="${MINIO_ROOT_USER}" \
  --set minio.auth.rootPassword="${MINIO_ROOT_PASSWORD}" \
  --set minio.scopedUsers.trino.secretKey="${MINIO_TRINO_SECRET_KEY}" \
  --set minio.scopedUsers.airflowLogs.secretKey="${MINIO_AIRFLOW_LOGS_SECRET_KEY}" \
  --set icebergCatalog.database.password="${ICEBERG_CATALOG_DATABASE_PASSWORD}" \
  --set icebergCatalog.pgEncryptionKey="${ICEBERG_CATALOG_PG_ENCRYPTION_KEY}" \
  --set trino.auth.oauth2ClientSecret="${TRINO_OAUTH2_CLIENT_SECRET}" \
  --set trino.auth.internalSharedSecret="${TRINO_INTERNAL_SHARED_SECRET}" \
  --set trino.auth.internalPassword="${TRINO_INTERNAL_PASSWORD}" \
  --set grafana.auth.adminPassword="${GRAFANA_ADMIN_PASSWORD}" \
  --set airflow.auth.jwtSecret="${AIRFLOW_JWT_SECRET}" \
  --set airflow.fernetKey="${AIRFLOW_FERNET_KEY}" \
  --set airflow.database.password="${AIRFLOW_DATABASE_PASSWORD}" \
  --set marquez.database.password="${MARQUEZ_DATABASE_PASSWORD}" \
  --set api.portalSessionSecret="${API_PORTAL_SESSION_SECRET}" \
  --set keycloak.admin.username="${KEYCLOAK_ADMIN_USERNAME}" \
  --set keycloak.admin.password="${KEYCLOAK_ADMIN_PASSWORD}" \
  --set keycloak.database.password="${KEYCLOAK_DATABASE_PASSWORD}" \
  --set keycloak.clientSecrets.capiba-dashboard="${KEYCLOAK_CLIENT_SECRET_CAPIBA_DASHBOARD}" \
  --set keycloak.clientSecrets.minio="${KEYCLOAK_CLIENT_SECRET_MINIO}" \
  --set keycloak.clientSecrets.grafana="${KEYCLOAK_CLIENT_SECRET_GRAFANA}" \
  --set keycloak.clientSecrets.airflow="${KEYCLOAK_CLIENT_SECRET_AIRFLOW}" \
  --set keycloak.clientSecrets.capiba-services="${KEYCLOAK_CLIENT_SECRET_CAPIBA_SERVICES}" \
  --set keycloak.devUser.username="${KEYCLOAK_DEV_USERNAME}" \
  --set keycloak.devUser.password="${KEYCLOAK_DEV_PASSWORD}" \
  --set keycloak.devUser.email="${KEYCLOAK_DEV_EMAIL}" \
  --wait \
  --timeout 10m \
  "${EXTRA_ARGS[@]}"

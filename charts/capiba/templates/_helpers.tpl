{{/*
Expand the name of the chart.
*/}}
{{- define "capiba.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "capiba.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "capiba.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "capiba.labels" -}}
helm.sh/chart: {{ include "capiba.chart" . }}
{{ include "capiba.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "capiba.selectorLabels" -}}
app.kubernetes.io/name: {{ include "capiba.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Component labels helper
*/}}
{{- define "capiba.componentLabels" -}}
app.kubernetes.io/component: {{ .component | quote }}
{{- end }}

{{/*
Postgres service name
*/}}
{{- define "capiba.postgresql.fullname" -}}
{{- printf "%s-postgresql" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Redis service name
*/}}
{{- define "capiba.redis.fullname" -}}
{{- printf "%s-redis" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
MinIO service name
*/}}
{{- define "capiba.minio.fullname" -}}
{{- printf "%s-minio" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Airflow service name
*/}}
{{- define "capiba.airflow.fullname" -}}
{{- printf "%s-airflow" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
ArangoDB service name
*/}}
{{- define "capiba.arangodb.fullname" -}}
{{- printf "%s-arangodb" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
API service name
*/}}
{{- define "capiba.api.fullname" -}}
{{- printf "%s-api" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Iceberg catalog (Lakekeeper) service name
*/}}
{{- define "capiba.icebergCatalog.fullname" -}}
{{- printf "%s-iceberg-catalog" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Marquez (data catalog/lineage) service name
*/}}
{{- define "capiba.marquez.fullname" -}}
{{- printf "%s-marquez" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Trino service name
*/}}
{{- define "capiba.trino.fullname" -}}
{{- printf "%s-trino" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Keycloak (SSO identity provider) service name
*/}}
{{- define "capiba.keycloak.fullname" -}}
{{- printf "%s-keycloak" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Grafana service name
*/}}
{{- define "capiba.grafana.fullname" -}}
{{- printf "%s-grafana" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Prometheus service name
*/}}
{{- define "capiba.prometheus.fullname" -}}
{{- printf "%s-prometheus" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Kepler (energy metrics) service name
*/}}
{{- define "capiba.kepler.fullname" -}}
{{- printf "%s-kepler" (include "capiba.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Storage class helper
*/}}
{{- define "capiba.storageClass" -}}
{{- if .Values.global.storageClass }}
{{- .Values.global.storageClass }}
{{- else if .persistence.storageClass }}
{{- .persistence.storageClass }}
{{- else }}
{{- "" }}
{{- end }}
{{- end }}

{{/*
Stable Fernet key for the Airflow metadata DB. Precedence: existing secret
(upgrades) > airflow.fernetKey value (survives fresh cluster installs whose
PostgreSQL hostPath data carries connections encrypted with it) > random.
Without a fixed key each process generates an ephemeral one and encrypted
connections/variables become undecryptable (InvalidToken) across processes,
restarts and cluster rebuilds.
*/}}
{{- define "capiba.airflow.fernetKey" -}}
{{- $secretName := printf "%s-auth" (include "capiba.airflow.fullname" .) -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace $secretName -}}
{{- if and $existing (index $existing.data "AIRFLOW__CORE__FERNET_KEY") -}}
{{- index $existing.data "AIRFLOW__CORE__FERNET_KEY" | b64dec -}}
{{- else if .Values.airflow.fernetKey -}}
{{- .Values.airflow.fernetKey -}}
{{- else -}}
{{- /* 43 alphanumeric chars + padding decode to exactly 32 bytes (valid Fernet key) */ -}}
{{- randAlphaNum 43 | printf "%s=" -}}
{{- end -}}
{{- end -}}

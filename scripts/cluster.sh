#!/usr/bin/env bash
# Manages the native k3s cluster for Capiba: start, stop and remove.
#
# Usage:
#   ./scripts/cluster.sh start   # install/start k3s, Traefik, capiba chart and Headlamp
#   ./scripts/cluster.sh stop    # stop the k3s service
#   ./scripts/cluster.sh remove  # stop and uninstall k3s (destructive)
#
# The script is idempotent for 'start': re-running it only applies missing
# pieces. 'remove' uses the official k3s uninstaller and wipes local cluster
# state; hostPath data under data/ and services/ is left untouched.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KUBECONFIG_FILE="${HOME}/.kube/config"
export KUBECONFIG="${KUBECONFIG_FILE}"

step() { echo ""; echo "=== [$1] $2 ==="; }

cmd="${1:-}"
if [[ -z "${cmd}" ]]; then
  echo "ERROR: missing command. Usage: $(basename "$0") {start|stop|remove}"
  exit 1
fi

# -----------------------------------------------------------------------------
# start
# -----------------------------------------------------------------------------
start() {
  step 1 "Installing/starting k3s"
  if ! command -v k3s >/dev/null 2>&1; then
    # INSTALL_K3S_VERSION (e.g. v1.36.3+k3s1) pins the release; needed when
    # the k3s channel API (update.k3s.io) is down — without it the installer
    # falls back to a nonexistent GitHub release named "stable" and 404s.
    curl -sfL https://get.k3s.io | sudo env INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION:-}" sh -
  fi
  sudo systemctl enable --now k3s

  step 2 "Configuring kubeconfig"
  mkdir -p "$(dirname "${KUBECONFIG_FILE}")"
  # Ensure the k3s kubeconfig is readable by the user (matches --write-kubeconfig-mode 644).
  if [[ -f /etc/rancher/k3s/k3s.yaml ]]; then
    sudo chmod 644 /etc/rancher/k3s/k3s.yaml
  fi
  if [[ -s ${KUBECONFIG_FILE} ]] && ! kubectl config get-contexts default >/dev/null 2>&1; then
    cp "${KUBECONFIG_FILE}" "${KUBECONFIG_FILE}.bak-pre-capiba"
    echo "Previous kubeconfig backed up to ${KUBECONFIG_FILE}.bak-pre-capiba"
  fi
  if ! kubectl config get-contexts default >/dev/null 2>&1; then
    sudo cp /etc/rancher/k3s/k3s.yaml "${KUBECONFIG_FILE}"
    sudo chown "$(id -u):$(id -g)" "${KUBECONFIG_FILE}"
    chmod 600 "${KUBECONFIG_FILE}"
  fi
  kubectl config use-context default
  kubectl get nodes

  step 3 "Installing Traefik ingress controller"
  helm repo add traefik https://traefik.github.io/charts >/dev/null 2>&1 || true
  helm repo update traefik >/dev/null
  # Pinned ClusterIP + web/websecure exposed on 8088/8443: in-cluster OIDC
  # clients reach the SSO issuer (https://keycloak.capiba.local:8443 — pods
  # trust the self-signed cert via the capiba-tls CA mounted by the chart)
  # through the Traefik service via the CoreDNS rewrite in the next step.
  # clusterIP is immutable: reuse the existing one if the service is already
  # deployed.
  # NOTE: since chart traefik-41.x the service type key is service.spec.type
  # (the old service.type is silently ignored and defaults to LoadBalancer,
  # which makes k3s klipper/svclb grab the hostPorts and block the DaemonSet).
  TRAEFIK_CLUSTER_IP="$(kubectl -n traefik get svc traefik \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)"
  TRAEFIK_CLUSTER_IP="${TRAEFIK_CLUSTER_IP:-10.43.0.50}"
  helm upgrade --install traefik traefik/traefik --namespace traefik --create-namespace \
    --set deployment.kind=DaemonSet \
    --set updateStrategy.rollingUpdate.maxSurge=0 \
    --set updateStrategy.rollingUpdate.maxUnavailable=1 \
    --set ports.web.hostPort=8088 \
    --set ports.web.exposedPort=8088 \
    --set ports.websecure.hostPort=8443 \
    --set ports.websecure.exposedPort=8443 \
    --set service.spec.type=ClusterIP \
    --set service.spec.clusterIP="${TRAEFIK_CLUSTER_IP}"

  step 4 "Configuring in-cluster DNS for the SSO issuer and S3 ingress (CoreDNS)"
  # keycloak.capiba.local maps to 127.0.0.1 on the host (/etc/hosts), but
  # pods must reach the same issuer URL inside the cluster. s3.capiba.local
  # is the HTTPS MinIO ingress used by Lakekeeper storage profiles; pods
  # reach it through Traefik so the same endpoint works inside and outside
  # the cluster. k3s merges the coredns-custom ConfigMap into CoreDNS.
  kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns-custom
  namespace: kube-system
data:
  capiba.server: |
    capiba.local:53 {
        errors
        hosts {
            ${TRAEFIK_CLUSTER_IP} keycloak.capiba.local
            ${TRAEFIK_CLUSTER_IP} s3.capiba.local
            fallthrough
        }
    }
EOF
  kubectl rollout restart deploy/coredns -n kube-system

  step 5 "Ensuring capiba images in k3s"
  # The chart uses locally-built images (capiba/api, capiba/airflow) with
  # pullPolicy IfNotPresent, but k3s has its own containerd store: build what
  # is missing in Docker and import what is missing into k3s.
  # Falls back to sudo when the user is not in the docker group.
  DOCKER=(docker)
  if ! docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  fi
  VERSION="$(grep -m1 '^version = ' "${PROJECT_ROOT}/pyproject.toml" | cut -d'"' -f2)"
  for spec in "api:Dockerfile" "airflow:Dockerfile.airflow"; do
    svc="${spec%%:*}"
    dockerfile="${spec##*:}"
    img="capiba/${svc}:${VERSION}"
    if ! "${DOCKER[@]}" image inspect "${img}" >/dev/null 2>&1; then
      echo "Image ${img} not found in Docker; building..."
      "${DOCKER[@]}" build -f "${PROJECT_ROOT}/${dockerfile}" -t "${img}" "${PROJECT_ROOT}"
    fi
    if ! sudo k3s ctr images ls -q 2>/dev/null | grep -q "capiba/${svc}:${VERSION}"; then
      echo "Importing ${img} into the k3s containerd..."
      "${DOCKER[@]}" save "${img}" | sudo k3s ctr images import -
    fi
  done

  step 6 "Installing the capiba chart"
  kubectl create namespace capiba --dry-run=client -o yaml | kubectl apply -f -
  "${PROJECT_ROOT}/scripts/gen-certs.sh"
  "${PROJECT_ROOT}/scripts/helm-upgrade.sh"
  kubectl -n capiba wait --for=condition=available deploy --all --timeout=10m || true

  step 7 "Installing Headlamp dashboard"
  helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/ >/dev/null 2>&1 || true
  helm repo update headlamp >/dev/null
  # SSO via Keycloak: public client "headlamp" (PKCE) in the capiba realm. The
  # pinned issuer resolves to Traefik (8443) both for the browser (/etc/hosts)
  # and for the pod (CoreDNS rewrite in step 4). Token login stays as fallback.
  helm upgrade --install headlamp headlamp/headlamp --namespace headlamp --create-namespace \
    --set config.oidc.clientID=headlamp \
    --set config.oidc.issuerURL=https://keycloak.capiba.local:8443/realms/capiba \
    --set config.oidc.scopes="openid profile email" \
    --set config.oidc.usePKCE=true \
    --set config.oidc.secret.create=false
  kubectl -n headlamp create serviceaccount headlamp-admin --dry-run=client -o yaml | kubectl apply -f -
  kubectl create clusterrolebinding headlamp-admin \
    --clusterrole=cluster-admin --serviceaccount=headlamp:headlamp-admin \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: headlamp-admin-token
  namespace: headlamp
  annotations:
    kubernetes.io/service-account.name: headlamp-admin
type: kubernetes.io/service-account-token
EOF

  step 8 "Cluster ready"
  cat <<EOF
  Cluster:   kubectl get nodes
  Capiba:    make cluster-status && make port-forward
  Portal:    https://api.capiba.local:8443/ (login SSO via Keycloak)
  Dashboard: make port-forward, then http://localhost:4466
             Login token: make dashboard-token
EOF
}

# -----------------------------------------------------------------------------
# stop
# -----------------------------------------------------------------------------
stop() {
  step 1 "Stopping k3s"
  sudo systemctl stop k3s || true
  echo "k3s stopped. Start again with: make cluster-start"
}

# -----------------------------------------------------------------------------
# remove
# -----------------------------------------------------------------------------
remove() {
  step 1 "Stopping k3s"
  sudo systemctl stop k3s || true

  step 2 "Uninstalling k3s"
  if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
    sudo /usr/local/bin/k3s-uninstall.sh
  else
    echo "WARNING: /usr/local/bin/k3s-uninstall.sh not found."
    echo "         k3s may have been installed manually or already removed."
  fi

  step 3 "Cleanup"
  rm -f "${KUBECONFIG_FILE}" "${KUBECONFIG_FILE}.bak-pre-capiba"
  # Remove kubeconfig generated as root by previous sudo runs.
  if [[ -f /root/.kube/config ]]; then
    sudo rm -f /root/.kube/config
  fi

  echo ""
  echo "Cluster removed. hostPath data under data/ and services/ was preserved."
  echo "To start a fresh cluster: make cluster-start"
}

# -----------------------------------------------------------------------------
# dispatch
# -----------------------------------------------------------------------------
case "${cmd}" in
  start)   start ;;
  stop)    stop ;;
  remove)  remove ;;
  *)
    echo "ERROR: unknown command '${cmd}'. Usage: $(basename "$0") {start|stop|remove}"
    exit 1
    ;;
esac

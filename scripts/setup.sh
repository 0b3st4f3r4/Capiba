#!/bin/bash
# Capiba Setup — Initial development environment installation

set -e

echo "=== Capiba Setup ==="

# Check system dependencies
command -v python3 >/dev/null 2>&1 || {
    echo "ERROR: python3 not found. Install Python 3.13+."
    exit 1
}

command -v uv >/dev/null 2>&1 || {
    echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
}

# Check Python version (>= 3.13)
PY_VERSION=$(python3 --version | cut -d' ' -f2)
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)'; then
    echo "ERROR: Python 3.13+ required. Current version: $PY_VERSION"
    exit 1
fi

# Create venv and install dependencies via Makefile (pip install -e ".[dev]")
echo "[1/6] Installing dependencies (make install-dev)..."
make install-dev

# Cluster tooling: docker, kubectl and helm are required by make cluster-start.
# The Docker daemon must be reachable by this user; when it is not and the
# user is outside the docker group, the user is added to it (active after
# re-login). Until then the Makefile and scripts/cluster.sh fall back to
# 'sudo docker' automatically.
echo "[2/6] Checking cluster tooling (docker, kubectl, helm)..."
if ! command -v docker >/dev/null 2>&1; then
    echo "      WARNING: docker not found."
    echo "         Install: https://docs.docker.com/engine/install/ubuntu/ (or: sudo apt install docker.io)"
elif docker info >/dev/null 2>&1; then
    echo "      Docker OK: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'daemon reachable')"
elif id -nG "$USER" | grep -qw docker; then
    echo "      WARNING: $USER is in the docker group but the daemon is unreachable."
    echo "         Start it with: sudo systemctl enable --now docker"
else
    echo "      Docker daemon is not accessible to $USER; adding to the docker group (may ask for sudo)..."
    if sudo usermod -aG docker "$USER" >/dev/null 2>&1; then
        echo "      Added $USER to the docker group. Log out/in (or 'newgrp docker') to activate."
        echo "      Until then, make and scripts/cluster.sh fall back to 'sudo docker'."
    else
        echo "      WARNING: could not add $USER to the docker group. Run manually:"
        echo "        sudo usermod -aG docker $USER   # then log out/in"
    fi
fi

for tool in kubectl helm; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "      WARNING: $tool not found (needed by make cluster-start)."
        case "$tool" in
            kubectl) echo "         Install: https://kubernetes.io/docs/tasks/tools/#kubectl" ;;
            helm)    echo "         Install: curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash" ;;
        esac
    fi
done

# Check RTK
if command -v rtk >/dev/null 2>&1; then
    echo "[3/6] RTK detected: $(rtk --version)"
    echo "[4/6] Initializing RTK for Kimi AI..."
    # Known upstream bug: current rtk versions reject '--agent kimi'
    # (possible values: claude, cursor, ...). Non-blocking by design: the
    # RTK section of AGENTS.md is already committed, so the error is
    # tolerated and the setup moves on.
    if rtk init --agent kimi; then
        echo "AGENTS.md generated with RTK rewrite rules."
    else
        echo "      WARNING: 'rtk init --agent kimi' failed (known RTK bug); continuing."
    fi
else
    echo "[3/6] WARNING: RTK not found."
    echo "         Install: brew install rtk"
    echo "         Or: curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh"
fi

# Map the in-cluster DNS names advertised by the Iceberg catalog to localhost,
# so host-side lake clients reach MinIO through the port-forward (make port-forward).
HOSTS_LINE="127.0.0.1 capiba-minio capiba-iceberg-catalog"
if grep -qE '\bcapiba-minio\b' /etc/hosts 2>/dev/null; then
    echo "[5/6] /etc/hosts already maps capiba-minio."
else
    echo "[5/6] Adding capiba-minio/capiba-iceberg-catalog to /etc/hosts (may ask for sudo)..."
    if echo "$HOSTS_LINE" | sudo tee -a /etc/hosts >/dev/null 2>&1; then
        echo "      Added: $HOSTS_LINE"
    else
        echo "      WARNING: could not edit /etc/hosts. Add it manually:"
        echo "        echo \"$HOSTS_LINE\" | sudo tee -a /etc/hosts"
    fi
fi

# Map the ingress hosts (<service>.capiba.local) to localhost: the Traefik
# ingress controller (DaemonSet) listens on the host's ports 8088/8443, so
# the UIs and APIs are reachable at https://<service>.capiba.local:8443
# (self-signed cert from scripts/gen-certs.sh) or http://...:8088 without
# port-forwards (port 80 belongs to the host's Apache).
INGRESS_HOSTS="api.capiba.local grafana.capiba.local marquez.capiba.local iceberg.capiba.local minio.capiba.local s3.capiba.local trino.capiba.local airflow.capiba.local keycloak.capiba.local"
if grep -qE '\bapi\.capiba\.local\b' /etc/hosts 2>/dev/null; then
    echo "[5/6] /etc/hosts already maps the *.capiba.local ingress hosts."
else
    echo "[5/6] Adding the *.capiba.local ingress hosts to /etc/hosts (may ask for sudo)..."
    if echo "127.0.0.1 $INGRESS_HOSTS" | sudo tee -a /etc/hosts >/dev/null 2>&1; then
        echo "      Added: 127.0.0.1 $INGRESS_HOSTS"
    else
        echo "      WARNING: could not edit /etc/hosts. Add it manually:"
        echo "        echo \"127.0.0.1 $INGRESS_HOSTS\" | sudo tee -a /etc/hosts"
    fi
fi

# Check Kimi Code CLI
if command -v kimi >/dev/null 2>&1; then
    echo "[6/6] Kimi Code CLI detected: $(kimi --version 2>/dev/null || echo 'unknown version')"
else
    echo "[6/6] WARNING: Kimi Code CLI not found."
    echo "         Install: curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash"
fi

echo ""
echo "=== Setup complete ==="
echo "Activate the environment: source .venv/bin/activate"
echo "Start the agent:          kimi"
echo "Check savings:            rtk gain"
echo ""
echo "Next steps:"
echo "  1. Configure .env (copy from .env.example)"
echo "  2. Run tests: make test"
echo "  3. Start the local cluster: make cluster-start"
echo "  4. Start agentic session: kimi"

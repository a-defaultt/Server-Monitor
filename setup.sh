#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup.sh — first-time deployment helper
# Generates a .env with random secrets, then starts the stack.
# Usage: chmod +x setup.sh && ./setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BLUE='\033[1;34m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Prerequisites ─────────────────────────────────────────────────────────────
command -v docker      >/dev/null 2>&1 || error "docker not found. Install Docker first."
command -v openssl     >/dev/null 2>&1 || error "openssl not found."

info "Checking Docker Compose..."
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    error "Neither 'docker compose' (plugin) nor 'docker-compose' (standalone) found."
fi
success "Using: $DC"

# ── Generate .env ─────────────────────────────────────────────────────────────
if [[ -f .env ]]; then
    warn ".env already exists — skipping generation."
    warn "Delete it and re-run to reset credentials."
else
    info "Generating .env with random credentials..."
    INFLUXDB_PW=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 24)
    INFLUXDB_TOKEN=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 64)
    GRAFANA_PW=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 20)

    cat > .env <<EOF
INFLUXDB_USERNAME=admin
INFLUXDB_PASSWORD=${INFLUXDB_PW}
INFLUXDB_ORG=monitoring
INFLUXDB_BUCKET=server_metrics
INFLUXDB_TOKEN=${INFLUXDB_TOKEN}

GRAFANA_USER=admin
GRAFANA_PASSWORD=${GRAFANA_PW}
EOF
    success ".env created."
fi

# ── Docker socket permission check ───────────────────────────────────────────
if [[ ! -r /var/run/docker.sock ]]; then
    warn "/var/run/docker.sock is not readable by current user."
    warn "Add yourself to the docker group:  sudo usermod -aG docker \$USER"
    warn "Then log out/in and re-run this script."
    warn "(Continuing anyway — docker metrics will be skipped if socket is unreadable.)"
fi

# ── Build & start ─────────────────────────────────────────────────────────────
info "Building collector image..."
$DC build --no-cache collector

info "Starting stack..."
$DC up -d

# ── Wait for Grafana ─────────────────────────────────────────────────────────
info "Waiting for Grafana to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:3000/api/health >/dev/null 2>&1; then
        break
    fi
    sleep 3
done

# ── Done ─────────────────────────────────────────────────────────────────────
source .env
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Server Monitor is running! 🎉               ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  📊 Grafana dashboard : ${BLUE}http://$(hostname -I | awk '{print $1}'):3000${NC}"
echo -e "     Login            : ${GRAFANA_USER} / ${GRAFANA_PASSWORD}"
echo ""
echo -e "  🗄️  InfluxDB UI      : ${BLUE}http://localhost:8086${NC}  (loopback only)"
echo -e "     Login            : ${INFLUXDB_USERNAME} / ${INFLUXDB_PASSWORD}"
echo ""
echo -e "  ℹ️  First metrics arrive ~60 seconds after start."
echo -e "  ℹ️  Data retained for 7 days automatically."
echo ""
echo -e "  Useful commands:"
echo -e "    $DC logs -f collector    # live collector output"
echo -e "    $DC ps                   # container status"
echo -e "    $DC down                 # stop everything"

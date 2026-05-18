#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install.sh — One-click installation script for Server Monitor
# 1. Installs Docker and system dependencies if missing.
# 2. Configures permissions.
# 3. Generates secure .env credentials.
# 4. Builds and starts the monitoring stack.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BLUE='\033[1;34m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Root Check ────────────────────────────────────────────────────────────────
if [[ $EUID -eq 0 ]]; then
   error "Do not run this script as root. Use a standard user with sudo privileges."
fi

# ── Dependency Installation ───────────────────────────────────────────────────
install_docker() {
    info "Docker not found. Attempting to install..."
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg lsb-release
    
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    sudo usermod -aG docker "$USER"
    success "Docker installed and user added to 'docker' group."
    warn "You may need to log out and back in for group changes to take effect."
}

info "Verifying system dependencies..."

# Check for Docker
if ! command -v docker >/dev/null 2>&1; then
    install_docker
else
    success "Docker is already installed."
fi

# Check for OpenSSL (required for password generation)
if ! command -v openssl >/dev/null 2>&1; then
    info "Installing openssl..."
    sudo apt-get install -y openssl
fi

# Determine Docker Compose command
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    error "Docker Compose not found even after installation attempt."
fi

# ── Generate .env ─────────────────────────────────────────────────────────────
if [[ -f .env ]]; then
    warn ".env already exists — skipping generation."
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
    success ".env created with secure secrets."
fi

# ── Build & Start ─────────────────────────────────────────────────────────────
info "Building and starting the monitoring stack..."

# Ensure we can read the docker socket (attempting a fix for current session)
sudo chmod 666 /var/run/docker.sock || warn "Could not chmod docker socket. If build fails, try logging out/in."

$DC build collector
$DC up -d

# ── Final Status ──────────────────────────────────────────────────────────────
info "Waiting for services to initialize..."
sleep 10

source .env
IP_ADDR=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           Installation Complete & Stack Running!         ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  📊 Grafana dashboard : ${BLUE}http://${IP_ADDR}:3000${NC}"
echo -e "     Login            : ${GRAFANA_USER} / ${GRAFANA_PASSWORD}"
echo ""
echo -e "  🗄️  InfluxDB UI      : ${BLUE}http://localhost:8086${NC}"
echo -e "     Login            : ${INFLUXDB_USERNAME} / ${INFLUXDB_PASSWORD}"
echo ""
echo -e "  Useful commands:"
echo -e "    $DC logs -f collector    # view metric collection"
echo -e "    $DC ps                   # check container health"
echo ""
warn "If the dashboard doesn't load immediately, wait 30 seconds for initialization."

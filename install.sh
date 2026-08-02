#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
#  NetTracker Install Script
#  Installs dependencies, CLI command, and systemd service
# ═══════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[•]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
err()     { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
SERVICE_NAME="nettracker"
SERVICE_FILE="$SCRIPT_DIR/systemd/nettracker.service"
INSTALL_USER="${SUDO_USER:-$(whoami)}"

echo ""
echo -e "${CYAN}  ███╗   ██╗███████╗████████╗████████╗██████╗  █████╗  ██████╗██╗  ██╗███████╗██████╗ ${NC}"
echo -e "${CYAN}  ████╗  ██║██╔════╝╚══██╔══╝╚══██╔══╝██╔══██╗██╔══██╗██╔════╝██║ ██╔╝██╔════╝██╔══██╗${NC}"
echo -e "${CYAN}  ██╔██╗ ██║█████╗     ██║      ██║   ██████╔╝███████║██║     █████╔╝ █████╗  ██████╔╝${NC}"
echo -e "${CYAN}  ██║╚██╗██║██╔══╝     ██║      ██║   ██╔══██╗██╔══██║██║     ██╔═██╗ ██╔══╝  ██╔══██╗${NC}"
echo -e "${CYAN}  ██║ ╚████║███████╗   ██║      ██║   ██║  ██║██║  ██║╚██████╗██║  ██╗███████╗██║  ██║${NC}"
echo -e "${CYAN}  ╚═╝  ╚═══╝╚══════╝   ╚═╝      ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝${NC}"
echo ""
info "Linux Network Traffic Monitor — Install Script"
echo ""

# ── Python check ──────────────────────────────────────
info "Checking Python 3.10+…"
if ! command -v python3 &>/dev/null; then
  err "python3 not found. Please install Python 3.10 or newer."
fi
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ]]; then
  err "Python 3.10+ required (found $PY_VERSION)"
fi
success "Python $PY_VERSION found"

# ── pip / venv ────────────────────────────────────────
info "Ensuring pip and venv are available…"
if ! python3 -m pip --version &>/dev/null 2>&1; then
  info "pip not found — installing via get-pip.py…"
  if command -v curl &>/dev/null; then
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3
  elif command -v wget &>/dev/null; then
    wget -qO- https://bootstrap.pypa.io/get-pip.py | python3
  else
    # Try apt as last resort
    sudo apt-get install -y python3-pip 2>/dev/null || err "Cannot install pip. Please install python3-pip manually."
  fi
fi

if ! python3 -m venv --help &>/dev/null 2>&1; then
  info "python3-venv not available — installing…"
  sudo apt-get install -y python3-venv 2>/dev/null || err "Cannot install python3-venv."
fi
success "pip and venv ready"

# ── Virtual environment ───────────────────────────────
info "Creating virtual environment at $VENV_DIR…"
python3 -m venv "$VENV_DIR"
success "Virtual environment created"

# ── Install dependencies ──────────────────────────────
info "Installing Python dependencies…"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
success "Dependencies installed"

# ── CLI wrapper ───────────────────────────────────────
info "Creating nettracker CLI command…"
CLI_WRAPPER="/usr/local/bin/nettracker"
sudo tee "$CLI_WRAPPER" > /dev/null <<EOF
#!/usr/bin/env bash
cd "$SCRIPT_DIR"
exec "$VENV_DIR/bin/python" -m nettracker.cli "\$@"
EOF
sudo chmod +x "$CLI_WRAPPER"
success "CLI available at: nettracker"

# ── Docker group check ────────────────────────────────
if ! groups "$INSTALL_USER" | grep -q docker; then
  warn "User '$INSTALL_USER' is not in the 'docker' group."
  warn "Container monitoring will be disabled."
  warn "Fix with: sudo usermod -aG docker $INSTALL_USER && newgrp docker"
else
  success "User '$INSTALL_USER' is in the docker group"
fi

# ── Systemd service ───────────────────────────────────
if command -v systemctl &>/dev/null && [[ -d /etc/systemd/system ]]; then
  info "Installing systemd service…"
  # Patch the service file with correct paths / user
  sed \
    -e "s|User=orangepi|User=$INSTALL_USER|g" \
    -e "s|Group=docker|Group=docker|g" \
    -e "s|/home/orangepi/traffic_stats|$SCRIPT_DIR|g" \
    "$SERVICE_FILE" \
    | sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null

  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
  success "systemd service installed and started"
  info "  Status:  sudo systemctl status $SERVICE_NAME"
  info "  Logs:    journalctl -u $SERVICE_NAME -f"
else
  warn "systemd not found — skipping service install"
  warn "Start manually with: python3 -m nettracker.main"
fi

echo ""
success "NetTracker installed successfully!"
echo ""
echo -e "  ${CYAN}Dashboard:${NC}  http://$(hostname -I | awk '{print $1}'):7654"
echo -e "  ${CYAN}CLI:${NC}        nettracker watch"
echo -e "  ${CYAN}API docs:${NC}   http://$(hostname -I | awk '{print $1}'):7654/api/docs"
echo ""

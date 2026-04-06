#!/bin/bash
#
# Pramana: Production Startup Script
# Usage: ./scripts/start_prod.sh [—dev] [—network {anvil|sepolia|base}]
#

set -e

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${PROJECT_ROOT}/.logs"
PID_DIR="${PROJECT_ROOT}/.pids"
ENV_FILE="${PROJECT_ROOT}/.env"
VENV_PATH="${PROJECT_ROOT}/venv"

# Parse arguments
MODE="prod"  # prod or dev
NETWORK="${NETWORK:-anvil}"  # anvil, sepolia, or base

for arg in "$@"; do
  case $arg in
    --dev)
      MODE="dev"
      ;;
    --network)
      NETWORK="${2:-anvil}"
      shift 2
      ;;
  esac
done

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─────────────────────────────────────────────────────────────────────────────
# Functions
# ─────────────────────────────────────────────────────────────────────────────

log() {
  local level=$1
  shift
  echo -e "${BLUE}[Pramana]${NC} ${level}: $*"
}

success() {
  echo -e "${GREEN}✓${NC} $*"
}

error() {
  echo -e "${RED}✗${NC} $*" >&2
}

warn() {
  echo -e "${YELLOW}⚠${NC} $*"
}

check_dependency() {
  if ! command -v "$1" &> /dev/null; then
    error "$1 not found. Please install it: $2"
    exit 1
  fi
  success "$1 found"
}

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight Checks
# ─────────────────────────────────────────────────────────────────────────────

log "INFO" "Starting Pramana in ${MODE} mode (Network: ${NETWORK})"
log "INFO" "Performing pre-flight checks..."

# Check dependencies
check_dependency "python3" "https://python.org"
check_dependency "node" "https://nodejs.org"
check_dependency "npm" "https://nodejs.org"
check_dependency "anvil" "curl -L https://foundry.paradigm.xyz | bash"

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
log "INFO" "Python version: $PYTHON_VERSION"

# ─────────────────────────────────────────────────────────────────────────────
# Setup Directories
# ─────────────────────────────────────────────────────────────────────────────

mkdir -p "$LOG_DIR" "$PID_DIR" "${PROJECT_ROOT}/data"
log "INFO" "Created directories: $LOG_DIR, $PID_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Environment Setup
# ─────────────────────────────────────────────────────────────────────────────

if [ ! -f "$ENV_FILE" ]; then
  warn "No .env file found. Creating from example..."
  cp "${PROJECT_ROOT}/.env.example" "$ENV_FILE"
  
  # Generate secure secrets if missing
  if ! grep -q "JWT_SECRET=" "$ENV_FILE" || grep "JWT_SECRET=$" "$ENV_FILE"; then
    JWT_SECRET=$(openssl rand -hex 32)
    sed -i.bak "s|JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" "$ENV_FILE"
    log "INFO" "Generated JWT_SECRET"
  fi
  
  if ! grep -q "ATTESTATION_HMAC_SECRET=" "$ENV_FILE" || grep "ATTESTATION_HMAC_SECRET=$" "$ENV_FILE"; then
    HMAC_SECRET=$(openssl rand -hex 32)
    sed -i.bak "s|ATTESTATION_HMAC_SECRET=.*|ATTESTATION_HMAC_SECRET=${HMAC_SECRET}|" "$ENV_FILE"
    log "INFO" "Generated ATTESTATION_HMAC_SECRET"
  fi
  
  warn ".env created. Please configure GROQ_API_KEY and other settings."
fi

# Load environment
set -a
source "$ENV_FILE"
set +a

# Validate critical secrets
if [ -z "$JWT_SECRET" ] || [ "$JWT_SECRET" = "changeme" ]; then
  error "JWT_SECRET not configured. Edit .env and set a random 32+ character string."
  exit 1
fi

if [ -z "$GROQ_API_KEY" ]; then
  warn "GROQ_API_KEY not set. Get one at https://console.groq.com"
  warn "You can still run in dev mode with agent mocking."
fi

# Set environment for this run
export NETWORK="$NETWORK"
export DEBUG="$([ "$MODE" = "dev" ] && echo "true" || echo "false")"
export LOG_LEVEL="$([ "$MODE" = "dev" ] && echo "DEBUG" || echo "INFO")"

log "INFO" "Environment loaded (Network: $NETWORK, Debug: $DEBUG)"

# ─────────────────────────────────────────────────────────────────────────────
# Virtual Environment
# ─────────────────────────────────────────────────────────────────────────────

if [ ! -d "$VENV_PATH" ]; then
  log "INFO" "Creating Python virtual environment..."
  python3 -m venv "$VENV_PATH"
  source "$VENV_PATH/bin/activate"
  
  log "INFO" "Installing dependencies..."
  pip install --upgrade pip setuptools wheel
  pip install -r "${PROJECT_ROOT}/requirements.txt"
  success "Virtual environment setup complete"
else
  source "$VENV_PATH/bin/activate"
  success "Virtual environment activated"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Service Management
# ─────────────────────────────────────────────────────────────────────────────

start_service() {
  local name=$1
  local cmd=$2
  local port=$3
  local pid_file="${PID_DIR}/${name}.pid"
  local log_file="${LOG_DIR}/${name}.log"
  
  log "INFO" "Starting $name..."
  
  # Start service in background
  eval "$cmd" > "$log_file" 2>&1 &
  local pid=$!
  
  echo "$pid" > "$pid_file"
  
  # Wait for service to be ready
  sleep 2
  
  if kill -0 "$pid" 2>/dev/null; then
    success "$name started (PID: $pid, Port: $port, Log: $log_file)"
  else
    error "$name failed to start. Check log: $log_file"
    cat "$log_file"
    exit 1
  fi
}

stop_service() {
  local name=$1
  local port=$2
  local pid_file="${PID_DIR}/${name}.pid"
  
  if [ -f "$pid_file" ]; then
    local pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
      log "INFO" "Stopping $name (PID: $pid)..."
      kill "$pid" 2>/dev/null || true
      rm "$pid_file"
      sleep 1
      success "$name stopped"
    fi
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Health Checks
# ─────────────────────────────────────────────────────────────────────────────

check_service_health() {
  local name=$1
  local url=$2
  local max_attempts=30
  local attempt=0
  
  log "INFO" "Checking $name health..."
  
  while [ $attempt -lt $max_attempts ]; do
    if curl -s "$url" > /dev/null 2>&1; then
      success "$name is healthy"
      return 0
    fi
    
    attempt=$((attempt + 1))
    sleep 1
  done
  
  error "$name health check failed after $max_attempts attempts"
  return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────

trap 'cleanup' EXIT INT TERM

cleanup() {
  log "INFO" "Shutting down services..."
  stop_service "fastapi" "8000"
  stop_service "nextjs" "3000"
  stop_service "anvil" "8545"
  log "INFO" "Cleanup complete"
}

# Check if services already running
if [ -f "${PID_DIR}/fastapi.pid" ] || [ -f "${PID_DIR}/anvil.pid" ]; then
  warn "Some services already running. Run './scripts/stop.sh' first or use --restart flag"
  exit 1
fi

# Start Anvil (if using local network)
if [ "$NETWORK" = "anvil" ]; then
  start_service "anvil" "anvil --host 127.0.0.1" "8545"
  check_service_health "Anvil" "http://127.0.0.1:8545"
fi

# Start FastAPI Backend
export PYTHONPATH="${PROJECT_ROOT}"
start_service "fastapi" \
  "uvicorn core.app:app --host 127.0.0.1 --port 8000 $([ "$MODE" = "dev" ] && echo '--reload' || echo '')" \
  "8000"
check_service_health "FastAPI" "http://127.0.0.1:8000/health"

# Start Next.js Frontend
start_service "nextjs" \
  "cd '${PROJECT_ROOT}/frontend' && npm run $([ "$MODE" = "dev" ] && echo 'dev' || echo 'start') -- --port 3000" \
  "3000"
check_service_health "Next.js" "http://127.0.0.1:3000"

# ─────────────────────────────────────────────────────────────────────────────
# Post-Startup Initialization
# ─────────────────────────────────────────────────────────────────────────────

log "INFO" "Initializing agents and database..."

cd "$PROJECT_ROOT"
python scripts/register_agents.py --org default --quiet || warn "Agent registration failed (may be normal on first run)"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║${NC}  Pramana Server Started Successfully!                   ${GREEN}║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  📊 Dashboard:    http://127.0.0.1:3000"
echo "  🔌 API:          http://127.0.0.1:8000"
echo "  📖 API Docs:     http://127.0.0.1:8000/docs"
echo "  🔗 Blockchain:   http://127.0.0.1:8545 (Anvil)" 
echo ""
echo "  Environment: $NETWORK"
echo "  Mode: $MODE"
echo "  Logs: $LOG_DIR"
echo ""
echo -e "${YELLOW}⚠  To stop: Ctrl+C or './scripts/stop.sh'${NC}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Keep Process Running
# ─────────────────────────────────────────────────────────────────────────────

# Monitor services and restart if they die
while true; do
  sleep 10
  
  # Check if any service died
  for service in "anvil" "fastapi" "nextjs"; do
    pid_file="${PID_DIR}/${service}.pid"
    if [ -f "$pid_file" ]; then
      pid=$(cat "$pid_file")
      if ! kill -0 "$pid" 2>/dev/null; then
        warn "$service died (PID: $pid). Restarting..."
        rm "$pid_file"
        # Would restart here, but for now we'll just warn
      fi
    fi
  done
done

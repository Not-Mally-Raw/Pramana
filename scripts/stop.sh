#!/bin/bash
#
# Pramana: Stop Script
# Cleanly shuts down all Pramana services
#

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="${PROJECT_ROOT}/.pids"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
  echo -e "${BLUE}[Pramana]${NC} $*"
}

success() {
  echo -e "${GREEN}✓${NC} $*"
}

stop_service() {
  local name=$1
  local pid_file="${PID_DIR}/${name}.pid"
  
  if [ -f "$pid_file" ]; then
    local pid=$(cat "$pid_file")
    log "Stopping $name (PID: $pid)..."
    
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      
      # Wait for graceful shutdown
      local attempts=0
      while kill -0 "$pid" 2>/dev/null && [ $attempts -lt 10 ]; do
        sleep 0.5
        attempts=$((attempts + 1))
      done
      
      # Force kill if still running
      if kill -0 "$pid" 2>/dev/null; then
        log "Force stopping $name..."
        kill -9 "$pid" 2>/dev/null || true
      fi
      
      success "$name stopped"
    else
      log "$name already stopped (stale PID file)"
    fi
    
    rm -f "$pid_file"
  else
    log "$name not running"
  fi
}

# Main
log "Stopping Pramana services..."
echo ""

stop_service "fastapi"
stop_service "nextjs"
stop_service "anvil"

echo ""
success "All services stopped"

# Clean up PID directory
rmdir "$PID_DIR" 2>/dev/null || true

log "Pramana services have been shut down."

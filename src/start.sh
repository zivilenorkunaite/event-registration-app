#!/bin/bash
# Databricks App Startup Script
# Runs both Node.js frontend and Python backend services
# Usage: bash start.sh

set -e

echo "🎬 Event Registration App - Startup Script"
echo "Running on: $(hostname 2>/dev/null || echo 'unknown')"
echo ""

# Ensure backend dependencies are installed
echo "📦 Checking Python dependencies..."
if ! python3 -c "import fastapi" 2>/dev/null; then
  echo "Installing Python dependencies..."
  python3 -m pip install -q -r backend/requirements.txt
fi

# Ensure frontend dependencies are installed  
if [ ! -d "node_modules" ]; then
  echo "📦 Installing Node.js dependencies..."
  npm install --quiet
fi

echo ""
echo "🚀 Starting services..."
echo ""

# Start backend in background
export BACKEND_PORT=${BACKEND_PORT:-8001}
export PYTHONUNBUFFERED=1

echo "Starting Backend (Python) on port $BACKEND_PORT..."
python3 backend/main.py &
BACKEND_PID=$!

# Wait a moment for backend to start
sleep 2

# Start frontend (in foreground so docker/k8s can manage it)
export PORT=${PORT:-8000}

echo "Starting Frontend (Node.js) on port $PORT..."
exec node start-all.js

# Cleanup on exit
trap "kill $BACKEND_PID 2>/dev/null || true" EXIT

#!/bin/bash
set -e

# Install Python dependencies
echo "Installing Python dependencies..."
python3 -m pip install -q -r backend/requirements.txt

# Start gunicorn with FastAPI
echo "Starting Gunicorn with FastAPI..."
cd backend
exec python3 -m gunicorn main:app -w 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000

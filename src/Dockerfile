# Multi-stage Dockerfile for Event Registration App
# Combines Node.js frontend and Python backend in one container

FROM node:20-alpine AS base

# Install Python (needed for pip and Python backend)
RUN apk add --no-cache python3 py3-pip

WORKDIR /app

# Copy package files and install Node dependencies
COPY package*.json ./
RUN npm install --production

# Copy backend Python requirements and install
COPY backend/requirements.txt ./backend/requirements.txt
RUN python3 -m pip install --no-cache-dir -r backend/requirements.txt

# Copy application source code
COPY . .

# Ensure scripts are executable
RUN chmod +x start-all.js start.sh

# Set environment variables
ENV NODE_ENV=production
ENV PYTHONUNBUFFERED=1

# Expose the frontend port
EXPOSE 8000

# Run the master startup script
CMD ["npm", "start"]

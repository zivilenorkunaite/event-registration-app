/**
 * Master startup script for Databricks App
 * Runs both Node.js frontend (port 8000) and Python backend (port 8001)
 *
 * This is the single entry point when running on Databricks Apps.
 * It manages both services and ensures they're both healthy before responding.
 */

const { spawn, spawnSync } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");

const FRONTEND_PORT = process.env.PORT || 8000;
const BACKEND_PORT = process.env.BACKEND_PORT || 8001;
const STARTUP_TIMEOUT = 120000; // 120 seconds to start - pip install can take time

// Store process references for cleanup
let frontendProcess = null;
let backendProcess = null;
let healthCheckInterval = null;

/**
 * Ensure Python dependencies are installed
 */
async function ensurePythonDependencies() {
  return new Promise((resolve, reject) => {
    console.log("📦 Checking Python dependencies...");

    // Check if fastapi is available
    const checkCmd = spawnSync("python3", ["-c", "import fastapi"], {
      stdio: "pipe",
      cwd: __dirname,
    });

    if (checkCmd.status === 0) {
      console.log("✅ Python dependencies already installed");
      resolve();
      return;
    }

    const requirementsPath = path.join(__dirname, "backend", "requirements.txt");
    if (!fs.existsSync(requirementsPath)) {
      reject(new Error("backend/requirements.txt not found"));
      return;
    }

    console.log("📦 Installing Python dependencies...");
    const pip = spawn("python3", ["-m", "pip", "install", "-r", "backend/requirements.txt"], {
      cwd: __dirname,
      stdio: "inherit", // Show all output directly
    });

    pip.on("close", (code) => {
      if (code === 0) {
        console.log("✅ Python dependencies installed");
        resolve();
      } else {
        reject(new Error(`pip install failed with code ${code} - see output above`));
      }
    });

    pip.on("error", (err) => {
      reject(new Error(`Failed to install Python dependencies: ${err.message}`));
    });
  });
}

/**
 * Ensure Node.js dependencies are installed
 */
async function ensureNodeDependencies() {
  return new Promise((resolve, reject) => {
    const nodeModulesPath = path.join(__dirname, "node_modules");
    
    if (fs.existsSync(nodeModulesPath)) {
      console.log("✅ Node.js dependencies already installed");
      resolve();
      return;
    }

    console.log("📦 Installing Node.js dependencies...");
    const npm = spawn("npm", ["install"], {
      cwd: __dirname,
      stdio: "inherit", // Show all output directly
      timeout: 120000, // 2 minute timeout
    });

    npm.on("close", (code) => {
      if (code === 0) {
        console.log("✅ Node.js dependencies installed");
        resolve();
      } else {
        reject(new Error(`npm install failed with code ${code} - see output above`));
      }
    });

    npm.on("error", (err) => {
      reject(new Error(`Failed to install Node.js dependencies: ${err.message}`));
    });
  });
}

// Graceful shutdown handler
async function shutdown(signal) {
  console.log(`\n📴 Received ${signal}, shutting down gracefully...`);

  if (healthCheckInterval) {
    clearInterval(healthCheckInterval);
  }

  // Kill both processes
  if (frontendProcess && !frontendProcess.killed) {
    console.log("Stopping frontend...");
    frontendProcess.kill("SIGTERM");
  }

  if (backendProcess && !backendProcess.killed) {
    console.log("Stopping backend...");
    backendProcess.kill("SIGTERM");
  }

  // Wait a bit for graceful shutdown
  await new Promise((resolve) => setTimeout(resolve, 2000));

  // Force kill if still running
  if (frontendProcess && !frontendProcess.killed) {
    frontendProcess.kill("SIGKILL");
  }
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill("SIGKILL");
  }

  console.log("✅ Shutdown complete");
  process.exit(0);
}

// Setup signal handlers
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

/**
 * Start the Node.js frontend server
 */
function startFrontend() {
  return new Promise((resolve, reject) => {
    console.log(`\n🚀 Starting Frontend (Node.js) on port ${FRONTEND_PORT}...`);

    frontendProcess = spawn("node", ["server.js"], {
      cwd: __dirname,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        PORT: FRONTEND_PORT,
      },
    });

    let started = false;

    frontendProcess.stdout.on("data", (data) => {
      const output = data.toString().trim();
      console.log(`[Frontend] ${output}`);

      if (output.includes("Frontend server is running")) {
        if (!started) {
          started = true;
          resolve();
        }
      }
    });

    frontendProcess.stderr.on("data", (data) => {
      console.log(`[Frontend] ${data.toString().trim()}`);
    });

    frontendProcess.on("error", (err) => {
      console.error(`❌ Failed to start frontend: ${err.message}`);
      reject(err);
    });

    frontendProcess.on("exit", (code) => {
      console.log(`⚠️  Frontend exited with code ${code}`);
      if (!started) {
        reject(new Error(`Frontend exited with code ${code} before starting`));
      }
    });
  });
}

/**
 * Start the Python backend server
 */
function startBackend() {
  return new Promise((resolve, reject) => {
    console.log(`\n🚀 Starting Backend (Python) on port ${BACKEND_PORT}...`);

    backendProcess = spawn("python3", ["backend/main.py"], {
      cwd: __dirname,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        BACKEND_PORT: BACKEND_PORT,
        PYTHONUNBUFFERED: "1",
      },
    });

    let started = false;

    backendProcess.stdout.on("data", (data) => {
      const output = data.toString().trim();
      if (output) {
        console.log(`[Backend] ${output}`);
      }

      if (output.includes("Uvicorn running on")) {
        if (!started) {
          started = true;
          resolve();
        }
      }
    });

    backendProcess.stderr.on("data", (data) => {
      const output = data.toString().trim();
      if (output) {
        console.log(`[Backend] ${output}`);
      }
    });

    backendProcess.on("error", (err) => {
      console.error(`❌ Failed to start backend: ${err.message}`);
      reject(err);
    });

    backendProcess.on("exit", (code) => {
      console.log(`⚠️  Backend exited with code ${code}`);
      if (!started) {
        reject(new Error(`Backend exited with code ${code} before starting`));
      }
    });
  });
}

/**
 * Health check for frontend
 */
function checkFrontendHealth() {
  return new Promise((resolve) => {
    const req = http.get(
      `http://localhost:${FRONTEND_PORT}/health`,
      { timeout: 5000 },
      (res) => {
        resolve(res.statusCode === 200);
      },
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.abort();
      resolve(false);
    });
  });
}

/**
 * Health check for backend
 */
function checkBackendHealth() {
  return new Promise((resolve) => {
    const req = http.get(
      `http://localhost:${BACKEND_PORT}/health`,
      { timeout: 5000 },
      (res) => {
        resolve(res.statusCode === 200);
      },
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.abort();
      resolve(false);
    });
  });
}

/**
 * Monitor and report on service health
 */
function startHealthMonitor() {
  let frontendHealthy = false;
  let backendHealthy = false;

  healthCheckInterval = setInterval(async () => {
    const fe = await checkFrontendHealth();
    const be = await checkBackendHealth();

    // Only log changes
    if (fe !== frontendHealthy) {
      frontendHealthy = fe;
      console.log(
        `${fe ? "✅" : "❌"} Frontend: ${fe ? "healthy" : "unhealthy"}`,
      );
    }

    if (be !== backendHealthy) {
      backendHealthy = be;
      console.log(
        `${be ? "✅" : "❌"} Backend: ${be ? "healthy" : "unhealthy"}`,
      );
    }

    // Report overall status
    if (fe && be) {
      console.log("📊 All services healthy ✅");
    } else if (!fe || !be) {
      console.log(
        `📊 Services status: Frontend ${fe ? "✅" : "❌"} Backend ${be ? "✅" : "❌"}`,
      );
    }
  }, 30000); // Check every 30 seconds
}

/**
 * Main startup sequence
 */
async function main() {
  try {
    console.log("🎬 Event Registration App - Master Startup Script");
    console.log(
      `📍 Environment: ${process.env.DATABRICKS_APP_NAME ? "Databricks" : "Local Development"}`,
    );
    console.log(`⏱️  Startup timeout: ${STARTUP_TIMEOUT}ms\n`);

    // Ensure all dependencies are installed first
    await ensureNodeDependencies();
    await ensurePythonDependencies();

    // Start both services with timeout
    const startupPromise = Promise.race([
      Promise.all([startFrontend(), startBackend()]),
      new Promise((_, reject) =>
        setTimeout(
          () => reject(new Error("Startup timeout exceeded")),
          STARTUP_TIMEOUT,
        ),
      ),
    ]);

    await startupPromise;

    console.log("\n✨ Both services started successfully!\n");
    console.log("📋 Service URLs:");
    console.log(`   Frontend:  http://localhost:${FRONTEND_PORT}`);
    console.log(`   Backend:   http://localhost:${BACKEND_PORT}`);
    console.log(`   Health:    http://localhost:${FRONTEND_PORT}/health`);
    console.log("\n🔗 Frontend calls Backend at: http://localhost:8001");
    console.log(
      `\n💡 Running on ${process.env.DATABRICKS_APP_NAME ? "Databricks App" : "Local Machine"}`,
    );

    // Start health monitoring
    startHealthMonitor();
  } catch (error) {
    console.error(`\n❌ Startup failed: ${error.message}`);
    console.error("\n🔧 Troubleshooting:");
    console.error("   1. Check that Node.js and Python are installed");
    console.error(
      "   2. Verify backend requirements: pip install -r backend/requirements.txt",
    );
    console.error("   3. Ensure ports 8000 and 8001 are available");
    console.error("   4. Check DATABRICKS_* environment variables are set");

    // Attempt cleanup
    if (frontendProcess) frontendProcess.kill();
    if (backendProcess) backendProcess.kill();

    process.exit(1);
  }
}

// Start the application
main();

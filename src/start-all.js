/**
 * Master startup script for Event Registration App
 * Starts Node.js server which orchestrates both frontend and Python backend
 */

const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const STARTUP_TIMEOUT = 120000; // 120 seconds
let serverProcess = null;

/**
 * Find Python executable with packages installed
 */
function findPythonWithPackages() {
  const candidates = ["python3", "python", `/usr/bin/python3`, `/usr/bin/python`];
  
  for (const python of candidates) {
    try {
      const result = spawnSync(python, ["-c", "import fastapi; print('ok')"], {
        stdio: "pipe",
        encoding: "utf-8",
        cwd: __dirname,
      });
      
      if (result.status === 0) {
        console.log(`✅ Found Python with packages: ${python}`);
        return python;
      }
    } catch (e) {
      // Try next candidate
    }
  }
  
  return "python3"; // Default fallback
}

/**
 * Ensure Node dependencies are installed
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
      stdio: "inherit",
      timeout: 120000,
    });

    npm.on("close", (code) => {
      if (code === 0) {
        console.log("✅ Node.js dependencies installed");
        resolve();
      } else {
        reject(new Error(`npm install failed with code ${code}`));
      }
    });

    npm.on("error", (err) => {
      reject(new Error(`Failed to install Node.js dependencies: ${err.message}`));
    });
  });
}

/**
 * Ensure Python dependencies are installed
 */
async function ensurePythonDependencies() {
  return new Promise((resolve, reject) => {
    console.log("📦 Checking Python dependencies...");

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

    // Try pip3 install
    const pip = spawn("pip3", ["install", "-r", requirementsPath], {
      cwd: __dirname,
      stdio: "inherit",
      timeout: 120000,
    });

    pip.on("close", (code) => {
      if (code === 0) {
        console.log("✅ Python dependencies installed");
        resolve();
      } else {
        console.log("⚠️  pip3 failed, trying python3 -m pip...");
        
        const pythonPip = spawn("python3", ["-m", "pip", "install", "-r", requirementsPath], {
          cwd: __dirname,
          stdio: "inherit",
        });

        pythonPip.on("close", (pythonCode) => {
          if (pythonCode === 0) {
            console.log("✅ Python dependencies installed");
            resolve();
          } else {
            reject(new Error(`Failed to install Python dependencies`));
          }
        });
      }
    });

    pip.on("error", (err) => {
      reject(new Error(`Failed to run pip3: ${err.message}`));
    });
  });
}

/**
 * Start the Node.js server (which starts frontend + backend)
 */
function startServer() {
  return new Promise((resolve, reject) => {
    console.log("\n🚀 Starting Node.js server...");

    const pythonExe = findPythonWithPackages();

    serverProcess = spawn("node", ["server.js"], {
      cwd: __dirname,
      stdio: "inherit",
      env: {
        ...process.env,
        PYTHONEXE: pythonExe,
      },
      timeout: STARTUP_TIMEOUT,
    });

    serverProcess.on("error", (err) => {
      console.error(`❌ Failed to start server: ${err.message}`);
      reject(err);
    });

    // Give server time to start
    setTimeout(() => {
      console.log(`✅ Server started successfully!`);
      resolve();
    }, 3000);
  });
}

/**
 * Graceful shutdown handler
 */
async function shutdown(signal) {
  console.log(`\n📴 Received ${signal}, shutting down gracefully...`);

  if (serverProcess && !serverProcess.killed) {
    console.log("Stopping server...");
    serverProcess.kill("SIGTERM");
  }

  // Wait for graceful shutdown
  await new Promise((resolve) => setTimeout(resolve, 2000));

  // Force kill if still running
  if (serverProcess && !serverProcess.killed) {
    serverProcess.kill("SIGKILL");
  }

  console.log("✅ Shutdown complete");
  process.exit(0);
}

// Setup signal handlers
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

/**
 * Main startup sequence
 */
async function main() {
  try {
    console.log("🎬 Event Registration App - Server Startup");
    console.log(`📍 Environment: ${process.env.DATABRICKS_APP_NAME ? "Databricks Apps" : "Local Development"}\n`);

    // Ensure dependencies
    await ensureNodeDependencies();
    await ensurePythonDependencies();

    // Start server (which will start both frontend and backend)
    await startServer();

    const PORT = process.env.PORT || 8000;
    console.log(`\n✨ App is running!`);
    console.log(`📍 Frontend: http://localhost:${PORT}`);
    console.log(`📍 Health check: http://localhost:${PORT}/health\n`);
  } catch (error) {
    console.error(`\n❌ Startup failed: ${error.message}`);
    console.error(`\n🔧 Troubleshooting:`);
    console.error(`   1. Ensure Node.js 18+ is installed`);
    console.error(`   2. Ensure Python 3.8+ is installed`);
    console.error(`   3. Run: pip install -r backend/requirements.txt`);
    console.error(`   4. Check ports 8000 and 8001 are available\n`);

    if (serverProcess) serverProcess.kill();
    process.exit(1);
  }
}

// Start the app
main();

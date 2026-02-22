/**
 * Master startup orchestrator for Event Registration App
 * Manages both Node.js frontend and Python backend processes
 */

const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const FRONTEND_PORT = process.env.PORT || 8000;
const BACKEND_PORT = process.env.BACKEND_PORT || 8001;
const STARTUP_TIMEOUT = 120000;

let frontendProcess = null;
let backendProcess = null;
let pythonExecutable = "python3";

/**
 * Find Python executable that has required packages
 */
function findPythonWithPackages() {
  const candidates = [
    "python3",
    "python",
    "/usr/bin/python3",
    "/usr/bin/python",
  ];

  for (const python of candidates) {
    try {
      const result = spawnSync(python, ["-c", "import fastapi; print('ok')"], {
        stdio: "pipe",
        encoding: "utf-8",
        timeout: 5000,
      });

      if (result.status === 0) {
        return python;
      }
    } catch (e) {
      // Try next candidate
    }
  }

  return "python3";
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
      reject(err);
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
      timeout: 5000,
    });

    if (checkCmd.status === 0) {
      console.log("✅ Python dependencies already installed");
      resolve();
      return;
    }

    const requirementsPath = path.join(
      __dirname,
      "backend",
      "requirements.txt",
    );
    if (!fs.existsSync(requirementsPath)) {
      reject(new Error("backend/requirements.txt not found"));
      return;
    }

    console.log("📦 Installing Python dependencies...");

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

        const pythonPip = spawn(
          "python3",
          ["-m", "pip", "install", "-r", requirementsPath],
          {
            cwd: __dirname,
            stdio: "inherit",
          },
        );

        pythonPip.on("close", (pythonCode) => {
          if (pythonCode === 0) {
            console.log("✅ Python dependencies installed");
            resolve();
          } else {
            reject(new Error("Failed to install Python dependencies"));
          }
        });
      }
    });

    pip.on("error", (err) => {
      reject(err);
    });
  });
}

/**
 * Start Node.js frontend server
 */
function startFrontend() {
  return new Promise((resolve, reject) => {
    console.log(`\n🚀 Starting Frontend (Node.js) on port ${FRONTEND_PORT}...`);

    frontendProcess = spawn("node", ["server.js"], {
      cwd: __dirname,
      stdio: "inherit",
      env: {
        ...process.env,
        PORT: FRONTEND_PORT,
      },
    });

    frontendProcess.on("error", (err) => {
      reject(err);
    });

    frontendProcess.on("exit", (code) => {
      if (code !== 0 && code !== null) {
        console.error(`⚠️  Frontend exited with code ${code}`);
      }
    });

    setTimeout(() => {
      console.log(`✅ Frontend started`);
      resolve();
    }, 1500);
  });
}

/**
 * Start Python backend server
 */
function startBackend() {
  return new Promise((resolve, reject) => {
    console.log(`\n🚀 Starting Backend (Python) on port ${BACKEND_PORT}...`);

    backendProcess = spawn(pythonExecutable, ["backend/main.py"], {
      cwd: __dirname,
      stdio: "inherit",
      env: {
        ...process.env,
        BACKEND_PORT: BACKEND_PORT,
        PYTHONUNBUFFERED: "1",
      },
    });

    backendProcess.on("error", (err) => {
      reject(err);
    });

    backendProcess.on("exit", (code) => {
      if (code !== 0 && code !== null) {
        console.error(`⚠️  Backend exited with code ${code}`);
      }
    });

    setTimeout(() => {
      console.log(`✅ Backend started`);
      resolve();
    }, 2000);
  });
}

/**
 * Graceful shutdown
 */
function cleanup(signal) {
  console.log(`\n📴 Received ${signal}, shutting down...`);

  if (frontendProcess && !frontendProcess.killed) {
    frontendProcess.kill("SIGTERM");
  }

  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill("SIGTERM");
  }

  setTimeout(() => {
    if (frontendProcess && !frontendProcess.killed) {
      frontendProcess.kill("SIGKILL");
    }
    if (backendProcess && !backendProcess.killed) {
      backendProcess.kill("SIGKILL");
    }
    process.exit(0);
  }, 2000);
}

process.on("SIGTERM", () => cleanup("SIGTERM"));
process.on("SIGINT", () => cleanup("SIGINT"));

/**
 * Main startup sequence
 */
async function main() {
  try {
    console.log("🎬 Event Registration App - Starting\n");

    // Install dependencies
    await ensureNodeDependencies();
    await ensurePythonDependencies();

    // Detect Python environment
    console.log("\n🔍 Detecting Python environment...");
    pythonExecutable = findPythonWithPackages();
    console.log(`📍 Using Python: ${pythonExecutable}\n`);

    // Start both services
    await Promise.all([startFrontend(), startBackend()]);

    console.log(`\n✨ Both services started successfully!\n`);
    console.log(`📋 Service URLs:`);
    console.log(`   Frontend:  http://localhost:${FRONTEND_PORT}`);
    console.log(`   Backend:   http://localhost:${BACKEND_PORT}`);
    console.log(`   Health:    http://localhost:${FRONTEND_PORT}/health\n`);
  } catch (error) {
    console.error(`\n❌ Startup failed: ${error.message}\n`);
    process.exit(1);
  }
}

main();

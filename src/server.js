// Load environment variables from .env file (for local development)
require("dotenv").config();

const express = require("express");
const compression = require("compression");
const helmet = require("helmet");
const path = require("path");
const cors = require("cors");
const { spawn } = require("child_process");

const app = express();

// Backend configuration - will start Python backend inline
let pythonProcess = null;
const BACKEND_PORT = process.env.BACKEND_PORT || 8001;

// Middleware
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// CORS for frontend
app.use(cors());

// Security middleware
app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'", "'unsafe-inline'"],
        styleSrc: ["'self'", "'unsafe-inline'", "https:"],
        imgSrc: ["'self'", "data:"],
        fontSrc: ["'self'", "https:", "data:"],
        connectSrc: ["'self'"],
      },
    },
  }),
);

// Middleware for response compression (gzip)
app.use(compression());

// Start Python backend as child process
function startPythonBackend() {
  return new Promise((resolve, reject) => {
    console.log(`🐍 Starting Python backend on port ${BACKEND_PORT}...`);
    
    // Find which python has the packages
    const pythonCandidates = ["python3", "python"];
    let pythonExe = pythonCandidates[0];
    
    pythonProcess = spawn(pythonExe, ["backend/main.py"], {
      cwd: __dirname,
      stdio: "inherit",
      env: {
        ...process.env,
        BACKEND_PORT: BACKEND_PORT,
        PYTHONUNBUFFERED: "1",
      },
    });

    pythonProcess.on("error", (err) => {
      console.error(`❌ Failed to start Python backend: ${err.message}`);
      reject(err);
    });

    // Give backend time to start (~2 seconds)
    setTimeout(() => {
      console.log(`⏱️  Giving backend time to initialize...`);
      
      // Test if backend is responsive
      const http = require("http");
      const healthCheck = () => {
        const req = http.get(
          `http://localhost:${BACKEND_PORT}/health`,
          { timeout: 2000 },
          (res) => {
            if (res.statusCode === 200) {
              console.log(`✅ Python backend is ready on port ${BACKEND_PORT}`);
              resolve();
            } else {
              console.log(`⚠️  Backend responded with ${res.statusCode}, retrying...`);
              setTimeout(healthCheck, 1000);
            }
          }
        );
        
        req.on("error", (err) => {
          console.log(`⏳ Backend not ready yet, retrying...`);
          setTimeout(healthCheck, 1000);
        });
      };
      
      healthCheck();
    }, 2000);
  });
}

// Initialize backend when server starts
let backendReady = false;

// Proxy all /api requests to the Python backend
app.use("/api", (req, res) => {
  if (!backendReady) {
    return res.status(503).json({ error: "Backend initializing, please try again" });
  }

  const http = require("http");
  const options = {
    hostname: "localhost",
    port: BACKEND_PORT,
    path: req.url,
    method: req.method,
    headers: {
      ...req.headers,
      host: `localhost:${BACKEND_PORT}`,
    },
    timeout: 15000,
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });

  proxyReq.on("error", (err) => {
    console.error(`❌ Backend connection error: ${err.message}`);
    res.status(503).json({ 
      error: "Backend service unavailable",
      details: err.message 
    });
  });

  proxyReq.on("timeout", () => {
    console.error("❌ Backend request timeout");
    proxyReq.destroy();
    res.status(504).json({ error: "Backend request timeout" });
  });

  if (req.method !== "GET" && req.method !== "HEAD") {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      proxyReq.end(body);
    });
  } else {
    proxyReq.end();
  }
});

// Serve static files from the 'public' directory
app.use(express.static(path.join(__dirname, "public")));

// Root endpoint - serve index.html
app.get("/", (req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

// Health check endpoint
app.get("/health", (req, res) => {
  res.status(200).json({ status: "ok", service: "frontend", backend: backendReady ? "ready" : "initializing" });
});

// SPA routing - fallback to index.html for unknown routes
app.use((req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

// Start server
const PORT = process.env.PORT || 8000;

// Initialize backend and then start frontend
(async () => {
  try {
    // Start Python backend
    await startPythonBackend();
    backendReady = true;
    
    // Start Node.js frontend server
    app.listen(PORT, "0.0.0.0", () => {
      console.log(`\n✅ Frontend server is running on port ${PORT}`);
      console.log(`📍 Health check: http://localhost:${PORT}/health`);
      console.log(`🔗 Backend: http://localhost:${BACKEND_PORT} (via /api proxy)`);
      console.log(`\n🎉 App is ready!\n`);
    });
  } catch (error) {
    console.error(`\n❌ Failed to start app: ${error.message}`);
    console.error(`\nMake sure Python backend dependencies are installed:`);
    console.error(`  pip install -r backend/requirements.txt\n`);
    process.exit(1);
  }
})();

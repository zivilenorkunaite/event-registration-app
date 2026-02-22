// Load environment variables from .env file (for local development)
require("dotenv").config();

const express = require("express");
const compression = require("compression");
const helmet = require("helmet");
const path = require("path");
const cors = require("cors");
const http = require("http");

const app = express();

const BACKEND_PORT = process.env.BACKEND_PORT || 8001;

// Middleware
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// CORS for frontend to call backend API
app.use(cors());

// Security middleware: set HTTP headers to prevent common vulnerabilities
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

// Proxy API requests to Python backend
app.use("/api", (req, res) => {
  const options = {
    hostname: "127.0.0.1",
    port: BACKEND_PORT,
    path: req.url,
    method: req.method,
    headers: {
      ...req.headers,
      host: `localhost:${BACKEND_PORT}`,
    },
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });

  proxyReq.on("error", (err) => {
    console.error(`[API Proxy] Error connecting to backend: ${err.message}`);
    res.status(503).json({ error: "Backend service unavailable", details: err.message });
  });

  if (req.method !== "GET" && req.method !== "HEAD") {
    req.pipe(proxyReq);
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
  res.status(200).json({ status: "ok", service: "frontend" });
});

// SPA routing - fallback to index.html for unknown routes
app.use((req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

// Start server
const PORT = process.env.PORT || 8000;
app.listen(PORT, "0.0.0.0", () => {
  console.log(`✅ Frontend server is running on port ${PORT}`);
  console.log(`📍 Health check: http://localhost:${PORT}/health`);
  console.log(`🔗 Backend API proxy: /api → http://localhost:${BACKEND_PORT}`);
});

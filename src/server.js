// Load environment variables from .env file (for local development)
require("dotenv").config();

const express = require("express");
const compression = require("compression");
const helmet = require("helmet");
const path = require("path");
const cors = require("cors");
const httpProxy = require("http-proxy");

const app = express();

// Create proxy for backend API requests
const backendUrl = `http://localhost:${process.env.BACKEND_PORT || 8001}`;
const proxy = httpProxy.createProxyServer({
  target: backendUrl,
  changeOrigin: true,
  pathRewrite: { "^/api": "/api" }, // Keep /api prefix
});

// Handle proxy errors gracefully
proxy.on("error", (err, req, res) => {
  console.error("Proxy error:", err);
  res.status(502).json({ error: "Backend service unavailable" });
});

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

// Proxy all /api requests to the Python backend
app.use("/api", (req, res) => {
  proxy.web(req, res);
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
  console.log(`🔗 Backend proxy: /api → ${backendUrl}`);
});

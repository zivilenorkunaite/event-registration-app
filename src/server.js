// Load environment variables from .env file (for local development)
// In production/Databricks Apps, environment variables are set externally
require("dotenv").config();

const express = require("express");
const compression = require("compression");
const helmet = require("helmet");
const { Pool } = require("pg");
const path = require("path");
const fs = require("fs").promises;
const FileStorage = require("./storage");
const {
  generateNameTag,
  LABEL_WIDTH_PX,
  LABEL_HEIGHT_PX,
} = require("./lib/nametag-image");
const allowEmailReuse =
  process.env.ALLOW_EMAIL_REUSE === "false" ? false : true;

const app = express();

let pool;
let storageType;

// Check if running in a Databricks environment by checking for DATABRICKS_APP_NAME
// which is set in app.yml when deployed as a Databricks App
if (process.env.DATABRICKS_APP_NAME) {
  console.log(
    "🚀 Running in Databricks environment, using Delta Lake storage.",
  );
  const DeltaStorage = require("./delta-storage");
  pool = new DeltaStorage();
  storageType = "delta";
} else {
  // Original logic for local development: check for Postgres credentials, otherwise use file storage.
  const useFileStorage =
    !process.env.DB_USER ||
    !process.env.DB_HOST ||
    !process.env.DB_NAME ||
    !process.env.DB_PASSWORD;

  if (useFileStorage) {
    console.log(
      "📁 Running locally, using file-based storage (no PostgreSQL credentials found).",
    );
    pool = new FileStorage();
    storageType = "file";
  } else {
    console.log("🐘 Running locally, using PostgreSQL database.");
    storageType = "postgres";
  }
}

// Databricks Apps inject the port via environment variables
const PORT = process.env.PORT || 8000;

// Middleware to parse JSON and URL-encoded bodies
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Security middleware: set HTTP headers to prevent common vulnerabilities
// Configure helmet with relaxed CSP for development (allow inline scripts)
app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'", "'unsafe-inline'"],
        styleSrc: ["'self'", "'unsafe-inline'", "https:"],
        imgSrc: ["'self'", "data:"],
        fontSrc: ["'self'", "https:", "data:"],
      },
    },
  }),
);

// Middleware for response compression (gzip)
app.use(compression());

// Serve static files from the 'public' directory
app.use(express.static(path.join(__dirname, "public")));

// PostgreSQL connection pool configuration (only if using PostgreSQL)
if (storageType === "postgres") {
  const poolConfig = {
    user: process.env.DB_USER,
    host: process.env.DB_HOST,
    database: process.env.DB_NAME,
    password: process.env.DB_PASSWORD,
    port: parseInt(process.env.DB_PORT || "5432", 10),
    // Connection pool settings for better performance
    max: 20, // Maximum number of clients in the pool
    idleTimeoutMillis: 30000, // Close idle clients after 30 seconds
    connectionTimeoutMillis: 2000, // Return an error after 2 seconds if connection could not be established
  };

  pool = new Pool(poolConfig);

  // Handle database connection errors
  pool.on("error", (err) => {
    console.error("Unexpected error on idle client", err);
    process.exit(-1);
  });
}

// Email validation regex - compiled once at module load
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Simple regex for backend email validation
const isValidEmail = (email) => {
  return EMAIL_REGEX.test(email);
};

// Input sanitization helper
const sanitizeInput = (str) => {
  if (typeof str !== "string") return "";
  return str.trim().substring(0, 255); // Limit length and trim whitespace
};

// Registration Endpoint
app.post("/api/register", async (req, res) => {
  const { firstName, lastName, company, email, contactPermission } = req.body;

  // Backend Validation
  if (!firstName || !lastName || !company || !email) {
    return res.status(400).json({ error: "All text fields are required." });
  }

  // Sanitize inputs
  const sanitizedFirstName = sanitizeInput(firstName);
  const sanitizedLastName = sanitizeInput(lastName);
  const sanitizedCompany = sanitizeInput(company);
  const sanitizedEmail = sanitizeInput(email).toLowerCase();

  // Validate sanitized inputs aren't empty
  if (
    !sanitizedFirstName ||
    !sanitizedLastName ||
    !sanitizedCompany ||
    !sanitizedEmail
  ) {
    return res.status(400).json({ error: "All text fields are required." });
  }

  if (!isValidEmail(sanitizedEmail)) {
    return res.status(400).json({ error: "Invalid email format." });
  }

  // Convert checkbox value to boolean
  const isOptedIn = contactPermission === "on" || contactPermission === true;

  try {
    const query = `
            INSERT INTO event_registrations (first_name, last_name, company, company_email, contact_permission)
            VALUES ($1, $2, $3, $4, $5)
        `;
    const values = [
      sanitizedFirstName,
      sanitizedLastName,
      sanitizedCompany,
      sanitizedEmail,
      isOptedIn,
    ];

    await pool.query(query, values);
    res.status(201).json({ message: "Check-in successful! Welcome." });
  } catch (error) {
    console.error("Database error:", error);

    // Handle specific database errors
    if (error.code === "23505") {
      // Unique constraint violation
      if (!allowEmailReuse) {
        return res
          .status(409)
          .json({ error: "You’re already checked in with this email." });
      }
      // If reuse is allowed, treat it as a successful check-in (idempotent)
      return res
        .status(200)
        .json({ message: "Check-in successful! Welcome back." });
    }

    res.status(500).json({
      error: "An error occurred while checking you in. Please try again.",
    });
  }
});

// Print name tag endpoint (uses @mmote/niimbluelib via niimblue-node server)
app.post("/api/print", async (req, res) => {
  const { firstName, company, groupName, location } = req.body;

  if (!firstName) {
    return res
      .status(400)
      .json({ error: "First name is required for printing." });
  }

  const nameDisplay = firstName.toString().trim().toUpperCase();
  const companyVal = (company || "").toString().trim();
  const group = (groupName || "Energy & Utilities Data Connect").toString();
  const loc = (location || "Sydney").toString();

  try {
    // Generate both SVG and PNG in a single operation (no duplicate generation)
    const { svg: svgContent, png: imageBuffer } = await generateNameTag({
      groupName: group,
      location: loc,
      name: nameDisplay,
      company: companyVal,
    });
    const imageBase64 = imageBuffer.toString("base64");

    // Generate filename with timestamp
    const timestamp = new Date()
      .toISOString()
      .replace(/[:.]/g, "-")
      .slice(0, -5);
    const baseFilename = `nametag_${nameDisplay.replace(/\s+/g, "_")}_${timestamp}`;
    const pngFilename = `${baseFilename}.png`;

    // Save PNG image based on storage type
    if (storageType === "delta") {
      // Save to Databricks UC Volume
      await pool.savePngToVolume(imageBuffer, pngFilename);
    } else {
      // Save to local filesystem
      const imagesDir = path.join(__dirname, "data", "images");
      await fs.mkdir(imagesDir, { recursive: true });
      const pngPath = path.join(imagesDir, pngFilename);
      await fs.writeFile(pngPath, imageBuffer);
      console.log(`Saved name tag PNG: ${pngFilename}`);
    }

    const serverUrl = (process.env.NIIMBOT_SERVER_URL || "").replace(/\/$/, "");
    if (!serverUrl) {
      console.log("Print (no printer server):", {
        groupName: group,
        location: loc,
        name: nameDisplay,
        company: companyVal,
      });
      return res.status(200).json({
        message:
          "Name tag image generated. Set NIIMBOT_SERVER_URL to send to printer.",
        data: {
          groupName: group,
          location: loc,
          name: nameDisplay,
          company: companyVal,
        },
      });
    }

    // Optional: connect to printer if transport/address are set
    const transport = process.env.NIIMBOT_TRANSPORT;
    const address = process.env.NIIMBOT_ADDRESS;
    if (transport && address) {
      const connectRes = await fetch(`${serverUrl}/connect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transport, address }),
      });
      if (!connectRes.ok) {
        const errText = await connectRes.text();
        console.error("Printer connect failed:", errText);
        return res.status(502).json({
          error:
            "Could not connect to printer. Check NIIMBOT_TRANSPORT and NIIMBOT_ADDRESS.",
        });
      }
    }

    // Send print job to niimblue-node server (uses @mmote/niimbluelib)
    const printRes = await fetch(`${serverUrl}/print`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        imageBase64,
        labelWidth: LABEL_WIDTH_PX,
        labelHeight: LABEL_HEIGHT_PX,
        printTask: process.env.NIIMBOT_PRINT_TASK || "B1",
        printDirection: "top",
        quantity: 1,
      }),
    });

    if (!printRes.ok) {
      const errText = await printRes.text();
      console.error("Print failed:", errText);
      return res.status(502).json({
        error:
          "Printer rejected the job. Check niimblue-node server and printer connection.",
      });
    }

    const result = await printRes.json();
    res.status(200).json({
      message: result.message || "Name tag sent to printer successfully.",
      data: {
        groupName: group,
        location: loc,
        name: nameDisplay,
        company: companyVal,
      },
    });
  } catch (error) {
    console.error("Print error:", error);
    res
      .status(500)
      .json({ error: "An error occurred while printing the name tag." });
  }
});

// Health check endpoint (useful for monitoring)
app.get("/health", async (req, res) => {
  try {
    await pool.query("SELECT 1");
    res.status(200).json({ status: "healthy", database: "connected" });
  } catch (error) {
    res.status(503).json({ status: "unhealthy", database: "disconnected" });
  }
});

// Initialize storage and start server
(async () => {
  // Initialize storage if the provider has an initialize method (e.g., FileStorage, DeltaStorage)
  if (typeof pool.initialize === "function") {
    await pool.initialize();
  }

  // Bind to 0.0.0.0 which is required for Databricks Apps
  app.listen(PORT, "0.0.0.0", () => {
    console.log(`✅ Server is running on port ${PORT}`);
    console.log(`📍 Health check available at http://localhost:${PORT}/health`);
    console.log(`📝 Registration form available at http://localhost:${PORT}`);

    if (storageType === "postgres") {
      console.log(`\nDatabase connection:`);
      console.log(`   Host: ${process.env.DB_HOST}`);
      console.log(`   Database: ${process.env.DB_NAME}`);
      console.log(`   User: ${process.env.DB_USER}`);
    } else {
      console.log(`\nStorage: File-based (./data/registrations.json)`);
    }
  });
})();

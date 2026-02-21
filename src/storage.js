const fs = require("fs").promises;
const path = require("path");

const DATA_FILE = path.join(__dirname, "data", "registrations.json");
const DATA_DIR = path.dirname(DATA_FILE);
const IMAGES_DIR = path.join(__dirname, "data", "images");

// Ensure data directory exists
async function ensureDataDirectory() {
  try {
    await fs.mkdir(DATA_DIR, { recursive: true });
  } catch (error) {
    // Directory might already exist, ignore error
  }
}

// Ensure images directory exists
async function ensureImagesDirectory() {
  try {
    await fs.mkdir(IMAGES_DIR, { recursive: true });
  } catch (error) {
    // Directory might already exist, ignore error
  }
}

// Save SVG image to file
async function saveNameTagImage(svgContent, filename) {
  await ensureImagesDirectory();
  const filepath = path.join(IMAGES_DIR, filename);
  await fs.writeFile(filepath, svgContent, "utf8");
  return filepath;
}

// Read all registrations from file
async function readRegistrations() {
  try {
    await ensureDataDirectory();
    const data = await fs.readFile(DATA_FILE, "utf8");
    return JSON.parse(data);
  } catch (error) {
    if (error.code === "ENOENT") {
      // File doesn't exist yet, return empty array
      return [];
    }
    throw error;
  }
}

// Write registrations to file
async function writeRegistrations(registrations) {
  await ensureDataDirectory();
  await fs.writeFile(DATA_FILE, JSON.stringify(registrations, null, 2), "utf8");
}

// File-based storage implementation
class FileStorage {
  constructor() {
    this.initialized = false;
    this.writeLock = Promise.resolve();
  }

  async initialize() {
    if (!this.initialized) {
      await ensureDataDirectory();
      // Initialize with empty array if file doesn't exist
      try {
        await readRegistrations();
      } catch (error) {
        if (error.code === "ENOENT") {
          await writeRegistrations([]);
        }
      }
      this.initialized = true;
    }
  }

  async query(text, values) {
    await this.initialize();

    // Parse SQL-like query (simple implementation for INSERT)
    if (text.includes("INSERT INTO event_registrations")) {
      // Serialize writes to prevent race conditions
      const operation = async () => {
        const registrations = await readRegistrations();

        // Extract values from parameterized query
        const newRegistration = {
          id:
            registrations.length > 0
              ? Math.max(...registrations.map((r) => r.id || 0)) + 1
              : 1,
          first_name: values[0],
          last_name: values[1],
          company: values[2],
          company_email: values[3],
          contact_permission: values[4],
          created_at: new Date().toISOString(),
        };

        // Check for duplicate email
        const emailExists = registrations.some(
          (r) =>
            r.company_email.toLowerCase() ===
            newRegistration.company_email.toLowerCase(),
        );

        if (emailExists) {
          const error = new Error("Duplicate email");
          error.code = "23505"; // PostgreSQL unique constraint violation code
          throw error;
        }

        registrations.push(newRegistration);
        await writeRegistrations(registrations);

        return {
          rows: [{ id: newRegistration.id }],
          rowCount: 1,
        };
      };

      // Chain the operation and ensure the lock is always released (updated)
      const resultPromise = this.writeLock.then(operation);
      this.writeLock = resultPromise.catch(() => {});
      return resultPromise;
    }

    // Handle SELECT queries (for health check)
    if (text.includes("SELECT 1") || text.includes("SELECT")) {
      return {
        rows: [{ "?column?": 1 }],
        rowCount: 1,
      };
    }

    throw new Error(`Unsupported query: ${text}`);
  }

  async connect() {
    await this.initialize();
    return true;
  }

  on(event, callback) {
    // File storage doesn't need connection error handling
    // but we implement it for compatibility
    if (event === "error") {
      // No-op for file storage
    }
  }
}

module.exports = FileStorage;
module.exports.saveNameTagImage = saveNameTagImage;

// src/delta-storage.js
const { DBSQLClient } = require("@databricks/sql");
const fs = require("fs").promises;
const path = require("path");

/**
 * A storage class for Databricks that mimics the behavior of the pg and file-based storage,
 * allowing it to be used as a drop-in replacement in server.js.
 */
class DeltaStorage {
  constructor() {
    if (!process.env.DATABRICKS_HOST || !process.env.DATABRICKS_WAREHOUSE_ID) {
      throw new Error(
        "Databricks connection settings are missing. Please set DATABRICKS_HOST, DATABRICKS_WAREHOUSE_ID in your environment.",
      );
    }
    const serverHostname = process.env.DATABRICKS_HOST;
    const httpPath =
      "/sql/1.0/warehouses/" + process.env.DATABRICKS_WAREHOUSE_ID;
    const clientId = process.env.DATABRICKS_CLIENT_ID;
    const clientSecret = process.env.DATABRICKS_CLIENT_SECRET;

    this.client = new DBSQLClient();
    this.connectionConfig = {
      authType: "databricks-oauth",
      host: serverHostname,
      path: httpPath,
      oauthClientId: clientId,
      oauthClientSecret: clientSecret,
    };
    this.session = null;

    // Allow specifying catalog and schema via environment variables, with defaults.
    this.catalog = process.env.DATABRICKS_CATALOG || "main";
    this.schema = process.env.DATABRICKS_SCHEMA || "default";

    // UC Volume configuration for storing images
    // volumePath comes from app.yml resources, format: /Volumes/<catalog>/<schema>/<volume_name>
    this.volumePath =
      process.env.DATABRICKS_VOLUME_PATH || "/Volumes/main/default/nametags";
  }

  /**
   * Initializes the connection to the Databricks SQL warehouse.
   * This is called automatically on the first query if not already connected.
   */
  async initialize() {
    // This check prevents reconnecting if a session is already active.
    if (this.session) {
      return;
    }
    try {
      // Connect to the Databricks warehouse with OAuth credentials
      await this.client.connect(this.connectionConfig);
      console.log("🔗 Successfully connected to Databricks SQL warehouse.");

      // Open a session for executing queries
      this.session = await this.client.openSession();
      console.log("📊 Successfully opened SQL session.");
    } catch (error) {
      console.error("❌ Failed to connect to Databricks:", error);
      // Exit gracefully if the connection fails, as the app cannot function.
      process.exit(1);
    }
  }

  /**
   * Executes a SQL query. This method transforms a standard pg-style query
   * into a Databricks-compatible parameterized query using ordinal parameters.
   * @param {string} sql - The SQL query string with positional placeholders ($1, $2).
   * @param {Array<any>} values - An array of values corresponding to the placeholders.
   * @returns {Promise<Object>} A promise that resolves to an object indicating success.
   */
  async query(sql, values) {
    if (!this.session) {
      await this.initialize();
    }

    try {
      // The original query might refer to a table name without its full catalog and schema.
      // This makes the table name fully qualified to ensure it's found correctly.
      // e.g., 'INSERT INTO event_registrations' becomes 'INSERT INTO `main`.`default`.event_registrations'
      let finalSql = sql.replace(
        /INTO event_registrations/i,
        "INTO `" + this.catalog + "`.`" + this.schema + "`.event_registrations",
      );

      // Convert pg-style positional parameters ($1, $2, etc.) to Databricks ordinal format (?)
      finalSql = finalSql.replace(/\$\d+/g, "?");

      // Use executeStatement with ordinal parameters
      const operation = await this.session.executeStatement(finalSql, {
        ordinalParameters: values || [],
      });

      // Wait for the operation to complete and fetch results
      await operation.fetchAll();
      await operation.close();

      // Mimic the pg library's successful return object.
      return { rowCount: 1 };
    } catch (error) {
      console.error("❌ Databricks query failed:", error);
      // Re-throw the error so the calling function in server.js can handle it.
      throw error;
    }
  }

  /**
   * Gets an OAuth access token using client credentials flow.
   * @returns {Promise<string>} OAuth access token
   */
  async getOAuthToken() {
    const clientId = process.env.DATABRICKS_CLIENT_ID;
    const clientSecret = process.env.DATABRICKS_CLIENT_SECRET;
    const host = process.env.DATABRICKS_HOST;

    if (!clientId || !clientSecret || !host) {
      throw new Error(
        "Missing OAuth credentials (CLIENT_ID, CLIENT_SECRET, HOST)",
      );
    }

    try {
      const response = await fetch(`https://${host}/oidc/token`, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
          grant_type: "client_credentials",
          client_id: clientId,
          client_secret: clientSecret,
          scope: "all-apis",
        }).toString(),
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(
          `OAuth token request failed: ${error.error_description || response.statusText}`,
        );
      }

      const data = await response.json();
      return data.access_token;
    } catch (error) {
      console.error("❌ Failed to get OAuth token:", error);
      throw error;
    }
  }

  /**
   * Saves a PNG image to the UC Volume using Databricks Files REST API.
   * @param {Buffer} imageBuffer - The PNG image data as a buffer
   * @param {string} filename - The filename for the image (e.g., 'nametag_JOHN_2026-02-22T10-30-45.png')
   * @returns {Promise<string>} A promise that resolves to the UC volume path of the saved image
   */
  async savePngToVolume(imageBuffer, filename) {
    const host = process.env.DATABRICKS_HOST;
    const volumePath = process.env.DATABRICKS_VOLUME_PATH;

    if (!host || !volumePath) {
      console.warn(
        "⚠️  Databricks host or volume path not available. Cannot upload to UC volume.",
      );
      return `${this.volumePath}/${filename}`;
    }

    try {
      // Get OAuth access token for API authentication
      const accessToken = await this.getOAuthToken();

      const filePath = `${volumePath}/${filename}`;
      const encodedPath = encodeURIComponent(filePath);

      // Use Databricks Files API to upload PNG to UC Volume
      const response = await fetch(
        `https://${host}/api/2.0/fs/files/${encodedPath}`,
        {
          method: "PUT",
          headers: {
            Authorization: `Bearer ${accessToken}`,
            "Content-Type": "application/octet-stream",
          },
          body: imageBuffer,
        },
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(
          `Databricks Files API error: ${errorData.message || response.statusText}`,
        );
      }

      console.log(`✅ Saved name tag PNG to UC Volume: ${filename}`);
      return filePath;
    } catch (error) {
      console.error(`❌ Failed to save image to UC Volume: ${error.message}`);
      throw error;
    }
  }

  /**
   * A no-op 'on' method for compatibility with the pg Pool interface, which server.js tries to call.
   */
  on(event, callback) {
    // This does nothing but prevents an error when server.js calls pool.on('error', ...).
  }
}

module.exports = DeltaStorage;

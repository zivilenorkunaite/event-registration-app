// src/delta-storage.js
const { DBSQLClient } = require("@databricks/sql");

/**
 * A storage class for Databricks that mimics the behavior of the pg and file-based storage,
 * allowing it to be used as a drop-in replacement in server.js.
 */
class DeltaStorage {
  constructor() {
    if (!process.env.DATABRICKS_HOST || !process.env.WAREHOUSE_ID) {
      throw new Error(
        "Databricks connection settings are missing. Please set DATABRICKS_HOST, WAREHOUSE_ID in your environment.",
      );
    }
    const serverHostname = process.env.DATABRICKS_HOST;
    const httpPath = "/sql/1.0/warehouses/" + process.env.WAREHOUSE_ID;
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
  }

  /**
   * Initializes the connection to the Databricks SQL warehouse.
   * This is called automatically on the first query if not already connected.
   */
  async initialize() {
    // This check prevents reconnecting if a session is already active.
    if (this.session && this.session.isOpen()) {
      return;
    }
    try {
      this.session = await this.client.connect(this.connectionConfig);
      console.log("🔗 Successfully connected to Databricks SQL warehouse.");
    } catch (error) {
      console.error("❌ Failed to connect to Databricks:", error);
      // Exit gracefully if the connection fails, as the app cannot function.
      process.exit(1);
    }
  }

  /**
   * Executes a SQL query. This method transforms a standard pg-style query
   * into a Databricks-compatible parameterized query.
   * @param {string} sql - The SQL query string with positional placeholders ($1, $2).
   * @param {Array<any>} values - An array of values corresponding to the placeholders.
   * @returns {Promise<Object>} A promise that resolves to an object indicating success.
   */
  async query(sql, values) {
    if (!this.session) {
      await this.initialize();
    }

    // The Databricks SDK uses named parameters (e.g., :p1, :p2) instead of positional ones ($1, $2).
    // This logic converts the query and values to the required format.
    const paramNames = [];
    let paramIndex = 1;
    const transformedSql = sql.replace(/\$[0-9]+/g, () => {
      const paramName = `p${paramIndex++}`;
      paramNames.push(paramName);
      return `:${paramName}`;
    });

    const parameters = {};
    for (let i = 0; i < values.length; i++) {
      parameters[paramNames[i]] = values[i];
    }

    // The original query might refer to a table name without its full catalog and schema.
    // This makes the table name fully qualified to ensure it's found correctly.
    // e.g., 'INSERT INTO event_registrations' becomes 'INSERT INTO `main`.`default`.event_registrations'
    const finalSql = transformedSql.replace(
      /INTO event_registrations/i,
      "INTO `" + this.catalog + "`.`" + this.schema + "`.event_registrations",
    );

    try {
      const statement = await this.session.executeStatement(finalSql, {
        parameters,
      });
      // The `RETURNING id` clause is not supported in the same way. We just run the INSERT.
      await statement.fetchAll(); // Executes the query.
      await statement.close();

      // Mimic the pg library's successful return object.
      return { rowCount: 1 };
    } catch (error) {
      console.error("❌ Databricks query failed:", error);
      // Re-throw the error so the calling function in server.js can handle it.
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

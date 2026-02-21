-- Event Registration App Database Schema for Databricks Delta Lake

DROP TABLE IF EXISTS event_registrations;

CREATE TABLE IF NOT EXISTS event_registrations (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    first_name STRING NOT NULL,
    last_name STRING NOT NULL,
    company STRING NOT NULL,
    company_email STRING NOT NULL,
    contact_permission BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported');

-- Create an index on email for faster lookups (optional but recommended)
CREATE INDEX IF NOT EXISTS idx_email ON event_registrations(company_email);

-- Optional: Add a unique constraint on email if you want to prevent duplicate registrations
-- ALTER TABLE event_registrations ADD CONSTRAINT unique_email UNIQUE (company_email);

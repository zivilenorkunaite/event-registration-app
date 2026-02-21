-- Event Registration App Database Schema

CREATE TABLE IF NOT EXISTS event_registrations (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    company VARCHAR(200) NOT NULL,
    company_email VARCHAR(255) NOT NULL,
    contact_permission BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create an index on email for faster lookups (optional but recommended)
CREATE INDEX IF NOT EXISTS idx_email ON event_registrations(company_email);

-- Optional: Add a unique constraint on email if you want to prevent duplicate registrations
-- ALTER TABLE event_registrations ADD CONSTRAINT unique_email UNIQUE (company_email);

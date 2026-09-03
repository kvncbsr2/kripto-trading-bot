-- Initialize TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Create default database if needed
COMMENT ON EXTENSION timescaledb IS 'TimescaleDB time-series database toolkit';

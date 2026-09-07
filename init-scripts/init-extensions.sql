-- Enable required extensions for liuhao AI OS
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS uuid_ossp;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector; -- for pgvector if needed later

-- Create initial roles and permissions
CREATE ROLE liuhao_app WITH LOGIN PASSWORD 'liuhao_secure_pass_2024' NOINHERIT;
GRANT CONNECT ON DATABASE liuhao_ai_os TO liuhao_app;
GRANT USAGE ON SCHEMA public TO liuhao_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO liuhao_app;

-- Create audit schema and user
CREATE SCHEMA IF NOT EXISTS audit;
CREATE ROLE audit_user WITH LOGIN PASSWORD 'audit_secure_pass_2024' NOINHERIT;
GRANT USAGE ON SCHEMA audit TO audit_user;
GRANT SELECT ON ALL TABLES IN SCHEMA audit TO audit_user;

COMMENT ON EXTENSION pg_stat_statements IS 'Query performance tracking';
COMMENT ON EXTENSION uuid_ossp IS 'UUID generation';
COMMENT ON EXTENSION pg_trgm IS 'Text similarity search';

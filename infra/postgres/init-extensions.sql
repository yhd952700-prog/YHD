-- LIUHAO AI OS PostgreSQL Init Extensions
-- This file runs once on initial database creation.
-- Add extensions and base configuration here.

-- uuid support for primary keys
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- crypto for token digests
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- citext for case-insensitive identifiers (emails, usernames)
CREATE EXTENSION IF NOT EXISTS citext;

-- btree_gist for exclusion constraints on goal/budget ranges
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- pg_trgm for memory_items full-text fuzzy search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

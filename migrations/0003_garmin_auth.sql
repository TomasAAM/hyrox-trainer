-- Garmin OAuth token store.
--
-- Garmin Connect blocks SSO password logins from datacentre IPs with a
-- Cloudflare bot challenge (HTTP 403), which is why the scheduled GitHub Action
-- could never authenticate. The garminconnect client can instead resume from a
-- previously issued DI token and refresh it without touching the SSO endpoint.
--
-- That refresh rotates the token, so it cannot live in a static secret: it has
-- to be read and written back on every run. This table is that store. It holds
-- exactly one row (id = 1), rewritten after each successful authentication.
--
-- SECURITY: `tokens` is a live credential granting full access to the Garmin
-- account. RLS is enabled with NO policies, so anon and authenticated roles are
-- denied outright; only the service-role key (which bypasses RLS) can read it.
-- Never expose this table through a client-side key.

CREATE TABLE IF NOT EXISTS garmin_auth (
    id          int PRIMARY KEY DEFAULT 1,
    tokens      text NOT NULL,             -- serialized garminconnect DI token JSON
    updated_at  timestamptz DEFAULT now(),
    CONSTRAINT garmin_auth_single_row CHECK (id = 1)
);

ALTER TABLE garmin_auth ENABLE ROW LEVEL SECURITY;

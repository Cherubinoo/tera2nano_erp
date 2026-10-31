-- Read-only database role for administrators who inspect t2n_erp in pgAdmin 4.
-- Run once as a PostgreSQL superuser, connected to the t2n_erp database:
--   psql -U postgres -d t2n_erp -v pw="'choose-a-strong-password'" -f docs/pgadmin_readonly_role.sql
--
-- Bank account numbers are stored encrypted (account_number_encrypted), so this role sees ciphertext only.
-- Credentials such as ERP password hashes and API tokens are excluded below.

CREATE ROLE t2n_erp_readonly LOGIN PASSWORD :pw NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE t2n_erp TO t2n_erp_readonly;
GRANT USAGE ON SCHEMA public TO t2n_erp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO t2n_erp_readonly;
REVOKE SELECT ON accounts_user, authtoken_token, django_session FROM t2n_erp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO t2n_erp_readonly;
-- Tables created later by migrations that hold credentials must be revoked again after `migrate`.

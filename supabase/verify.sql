SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled,
       has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon_access,
       has_table_privilege('authenticated', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS authenticated_access
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='r'
ORDER BY c.relname;
SELECT version_num FROM public.alembic_version;
SELECT count(*) AS index_count FROM pg_indexes WHERE schemaname='public';

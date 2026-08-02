# Database migrations

`*.up.sql` files are applied once in lexical order by `hiveblot.migrations.apply_migrations`.
Matching `*.down.sql` files document a practical manual rollback. Rollbacks are intentionally not
run automatically because deleting published provenance records is destructive.

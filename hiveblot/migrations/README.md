# Database migrations

`*.up.sql` files are applied once in lexical order by `hiveblot.migrations.apply_migrations`.
Matching `*.down.sql` files document a practical manual rollback. Rollbacks are intentionally not
run automatically because deleting published provenance records is destructive.

`0012_public_discovery_frontier` adds immutable discovery runs/observations, canonical frontier
records and aliases, typed source relationships, artifact-acquisition provenance, optimistic
scheduling state, and append-only frontier events.

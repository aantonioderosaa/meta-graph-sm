# Neo4j plugins (pinned)

GDS is **not** installed via `NEO4J_PLUGINS` (that env var always fetches the latest
compatible version from `graphdatascience.ninja`). The jar is mounted into `/plugins`.

| File | Version | SHA-256 |
|------|---------|---------|
| `neo4j-graph-data-science-2.12.0.jar` | 2.12.0 | see `SHA256SUMS` |

Verified against `neo4j:5.24-community`. `CALL gds.version()` must return `2.12.0`.

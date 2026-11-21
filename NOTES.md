# Design Notes & 100x Scaling
- Problems Chosen: Solved bulk custodian ingestion with per-row fault tolerance, idempotency/deduplication, point-in-time balance, immutable reversals, period summaries, compliance audit logs, and support data masking.
- Why: Real-world financial ledgers require zero data loss, strict multi-tenant isolation, immutable auditability, and defense against bad or duplicate inputs.
- Correctness: Decimal precision (no floats), currency whitelisting, future-date rejection, overdraft guards, and atomic transfers.
- What Breaks at 100x Load:
1. Database Locks: SQLite file contention bottlenecks under high concurrent writes; migrate to PostgreSQL with PgBouncer connection pooling.
2. Balance Latency: Recalculating balances from genesis becomes slow on high-volume clients; add materialized balance checkpoints (daily/monthly snapshots).
3. Synchronous Bulk Ingestion: Large multi-thousand row uploads block HTTP workers; offload ingestion to async streaming queues (Kafka / Celery / BullMQ).
4. Table Bloat: Full table scans slow down; partition ledger_transactions by tenant_id and effective_at range.
5. Resource Starvation: Runaway clients could monopolize database connections; implement per-tenant Redis token bucket rate limiting.
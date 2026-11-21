# AI Usage Log

### 1. Tools Used
- **Antigravity Assistant (powered by Gemini 3.8 Flash)**: System architecture design, schema drafting, FastAPI routing, and pytest test suite development.
- **Python / pytest / FastAPI**: Execution and verification of test assertions and ledger balance calculations.

### 2. Key Prompts
- *"see the task and fix everything and give me results"*: Analyzed prompt requirements from screenshot specifications (The Tenant Ledger Challenge, multi-tenant isolation, bulk ingestion resilience, immutable reversals, point-in-time balance, audit logging, support role least-privilege view).
- Prompted AI to architect an append-only ledger model with compensatory reversal transactions instead of mutating database records.
- Prompted AI to build fault-tolerant bulk ingestion returning line-item errors for invalid rows rather than failing all-or-nothing.

### 3. Corrections and Rejected Approaches
- **Corrected: Idempotency vs Heuristic Duplicate Detection**: Initially, duplicate hash checking interfered with distinct intentional transactions sharing the same timestamp when explicit `external_id` was supplied. Corrected the logic so that `external_id` serves as the authoritative idempotency key, while heuristic window hashing protects requests where no `external_id` is supplied.
- **Corrected: Test Session Concurrency**: Updated test dependency overrides to issue distinct SQLAlchemy sessions per request with `expire_on_commit=False` to prevent lock contention and stale attribute reload exceptions during rapid multi-threading.
- **Rejected: In-Place Transaction Editing / Soft Deletes**: Compliance and financial integrity prohibit mutating posted transactions. Rejected `UPDATE` or `DELETE` on financial records in favor of immutable append-only ledger entries linked to reversal IDs.
- **Rejected: Floating-Point Math**: Rejected `float` datatypes; strictly enforced Python `Decimal` and SQL `Numeric(18, 4)` to eliminate floating-point rounding errors.

# Race conditions in read-decide-write flows

When two requests read the same value and then write based on it, both can "win". Classic case: stock is 1, request A reads 1, B reads 1, A writes 0, B writes -1. The fix lives **in the database the project already uses**. Use this reference for negative stock, double bookings, inconsistent balances, coupons used beyond their limit, or any review of a flow that reads then writes. Engine-specific isolation and lock evidence: `sdd-database-design-migrations`.

## Quick reference

Fixes in order of preference:

1. **Unique constraint** when the rule is "only one may exist":
   ```sql
   ALTER TABLE bookings ADD CONSTRAINT booking_unique UNIQUE (professional_id, starts_at);
   ```
2. **Atomic conditional update** for bounded increments or decrements:
   ```sql
   UPDATE stock SET qty = qty - 1
   WHERE product_id = $1 AND qty >= 1;  -- 0 rows affected = out of stock
   ```
3. **Row lock** when the decision depends on several reads:
   ```sql
   BEGIN;
   SELECT * FROM accounts WHERE id = $1 FOR UPDATE;
   -- rules...
   UPDATE accounts SET balance = balance - $2 WHERE id = $1;
   COMMIT;
   ```

Also useful: `CHECK (qty >= 0)` as a safety net and `INSERT ... ON CONFLICT` for upserts.

## Procedure

1. **Map.** Find every place that reads a value, decides and then writes based on it (ORM or SQL): orders, stock, coupons, balance, credits, seats, invitations, schedules, counters, status transitions ("if pending, approve").
2. **Explain the concurrent scenario** for each in two or three lines: what two requests in the same millisecond do and the damage.
3. **Check existing data.** Propose read-only queries that detect already corrupted data (`SELECT * FROM stock WHERE qty < 0`, duplicated slots). Show the results; never fix data without approval.
4. **Plan.** Pick the simplest fix from the list above for each point and show the proposed diff, including every new migration in full. Get approval before changing a file or running a migration.
5. **Implement** approved items one at a time. With a conditional update, check affected rows and turn "0 rows" into a clear error (for example `409 out of stock`). Disabling the submit button in the client is welcome UX, not protection.
6. **Test concurrency.** For each fix, an integration test fires N simultaneous requests (`Promise.all`, threads, `asyncio.gather`) with stock or seats equal to 1 and checks exactly one success, the others refused, and consistent data.

## Pitfalls

- Do **not** solve it with Redis, a cache, a queue, an in-memory mutex or "a bigger time window": more infrastructure guarantees nothing across processes. If the database truly cannot solve a case, justify it in writing and get approval before proposing new infrastructure.
- An application-level `if (stock > 0)` before the update does not help: the race happens between the read and the write.
- `FOR UPDATE` works only inside a transaction and holds until commit; keep the transaction short and free of external API calls.
- Adding a constraint to a table with duplicates fails: report the duplicates and propose a cleanup; never delete data on your own.
- With Supabase/PostgREST, atomic operations usually need a SQL function (RPC), not two client calls.

## Verification

- Every read-decide-write flow is listed with its concurrent scenario explained.
- Each is protected in the database (constraint, conditional update or lock).
- Simultaneous-request tests pass with a single success.
- Existing inconsistent data was reported to the requester.

# Fault injection

This phase automates the two capstone fault scenarios against Aspire Shop resources:

1. PostgreSQL down
2. Redis down

The automation is intentionally conservative. It only controls Docker containers and requires an explicit match for the target resource.

## Script

Use:

```bash
./scripts/inject_aspire_fault.sh discover
./scripts/inject_aspire_fault.sh stop postgres
./scripts/inject_aspire_fault.sh start postgres
./scripts/inject_aspire_fault.sh stop redis
./scripts/inject_aspire_fault.sh start redis
```

Aliases accepted by the script:

- PostgreSQL: `postgres`, `db`, `catalogdb`
- Redis: `redis`, `basketcache`, `cache`

## Discovery behavior

The script looks for Docker container names containing:

- PostgreSQL: `postgres` or `catalogdb`
- Redis: `basketcache` or `redis`

If the match is ambiguous, the script stops and asks for an explicit override.

## Explicit overrides

If Aspire names your containers differently, set:

```bash
export IIRS_ASPIRE_POSTGRES_CONTAINER=<exact-container-name>
export IIRS_ASPIRE_REDIS_CONTAINER=<exact-container-name>
```

Then rerun the script.

## Example outage workflow

1. Start the local observability stack.
2. Run Aspire Shop with OTLP pointed at the collector.
3. Generate baseline traffic.
4. Discover target containers:

```bash
./scripts/inject_aspire_fault.sh discover
```

5. Stop PostgreSQL:

```bash
./scripts/inject_aspire_fault.sh stop postgres
```

6. Trigger traffic again and watch telemetry.
7. Run IIRS in live mode against the PostgreSQL alert fixture.
8. Recover:

```bash
./scripts/inject_aspire_fault.sh start postgres
```

Repeat the same pattern for Redis with `stop redis` and `start redis`.

## Validation

The repository includes a script-level test harness:

```bash
bash tests/test_fault_injection.sh
```

This does not touch real Docker containers. It uses a fake Docker shim to verify container discovery and action routing.

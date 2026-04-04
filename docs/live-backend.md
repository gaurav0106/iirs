# Live PLT backend

IIRS now supports a live telemetry mode that queries Prometheus, Loki, and Tempo directly.

## Enable it

```bash
export IIRS_TELEMETRY_BACKEND=plt
export IIRS_PROMETHEUS_URL=http://localhost:9090
export IIRS_LOKI_URL=http://localhost:3100
export IIRS_TEMPO_URL=http://localhost:3200
```

Optional:

```bash
export IIRS_TENANT_ID=tenant-a
export IIRS_HTTP_TIMEOUT_SECONDS=15
export IIRS_VERIFY_TLS=false
```

## What it queries

- Prometheus: `GET /api/v1/query_range`
- Loki: `GET /loki/api/v1/query_range`
- Tempo: `GET /api/search`

The live backend reuses the same query templates as the mock backend, so the agent prompts and evidence shape stay stable while the data source changes.

## Current limitations

1. Recent changes are still static scenario data, not a live deployment feed.
2. The parser is intentionally tolerant of minor Tempo response shape differences, but it still assumes the standard search API.
3. This does not provision the PLT stack; it only consumes existing endpoints.

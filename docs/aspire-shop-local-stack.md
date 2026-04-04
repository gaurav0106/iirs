# Aspire Shop local stack

This is the infrastructure phase for turning IIRS into a true local end-to-end demo.

It does two things:

1. starts a local Prometheus + Loki + Tempo + OpenTelemetry Collector stack
2. runs the upstream Aspire Shop sample with OTLP export pointed at that collector

## Prerequisites

- Docker and Docker Compose
- `.NET 10 SDK`
- Aspire CLI or a working `dotnet run` flow for the sample AppHost
- `git`

The upstream sample referenced here is `dotnet/aspire-samples`, specifically `samples/aspire-shop`.

## Step 1: Start the observability stack

From this repo:

```bash
./scripts/run_observability_stack.sh up
./scripts/run_observability_stack.sh ps
```

Exposed ports:

- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Tempo: `http://localhost:3200`
- OTel Collector OTLP gRPC: `localhost:4317`
- OTel Collector OTLP HTTP: `localhost:4318`
- OTel Collector Prometheus exporter: `localhost:9464`

## Step 2: Fetch Aspire Shop

```bash
./scripts/bootstrap_aspire_shop.sh
```

This clones the upstream sample into `.external/aspire-samples` by default.

## Step 3: Run Aspire Shop against the collector

```bash
cd .external/aspire-samples/samples/aspire-shop
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
aspire run
```

Alternative:

```bash
dotnet run --project AspireShop.AppHost
```

The upstream sample AppHost defines these core services and dependencies:

- `frontend`
- `catalogservice`
- `catalogdbmanager`
- `basketservice`
- PostgreSQL resource `postgres` with database `catalogdb`
- Redis resource `basketcache`

## Step 4: Generate traffic

Open the shop frontend from the Aspire output and:

1. browse the catalog
2. add items to the basket
3. refresh and repeat a few times

This should generate logs, metrics, and traces for the app services.

## Step 5: Verify telemetry landed

### Prometheus

```bash
curl -s http://localhost:9090/api/v1/targets
curl -s -G http://localhost:9090/api/v1/query --data-urlencode 'query=up'
```

### Loki

```bash
curl -s -G http://localhost:3100/loki/api/v1/query --data-urlencode 'query={service_name="catalogservice"}'
curl -s -G http://localhost:3100/loki/api/v1/query --data-urlencode 'query={service_name="basketservice"}'
```

### Tempo

```bash
curl -s -G http://localhost:3200/api/search --data-urlencode 'q={ resource.service.name = "catalogservice" }'
curl -s -G http://localhost:3200/api/search --data-urlencode 'q={ resource.service.name = "basketservice" }'
```

## Step 6: Run IIRS in live mode

From this repo:

```bash
export IIRS_TELEMETRY_BACKEND=plt
export IIRS_PROMETHEUS_URL=http://localhost:9090
export IIRS_LOKI_URL=http://localhost:3100
export IIRS_TEMPO_URL=http://localhost:3200

./.venv/bin/iirs run --alert-file fixtures/alerts/postgres_down.json --show-trace
```

What you should expect:

- IIRS no longer uses the mock backend
- trace artifacts include live citation sources
- Retriever evidence IDs look like `log.live.*`, `metric.live.*`, and `trace.live.*`

## What is still missing

This phase provisions the observability side and the sample bootstrap workflow, but it does not yet automate fault injection.

The next step after this is fault scenario automation:

1. reliably stop PostgreSQL for the sample
2. reliably stop Redis for the sample
3. verify the expected telemetry signatures for each outage

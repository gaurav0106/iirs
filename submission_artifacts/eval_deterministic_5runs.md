# IIRS Evaluation -- Deterministic Mode

- Generated at: `2026-04-11T11:51:09+00:00`
- Telemetry backend: `mock`
- Total runs: `10`
- Top-1 accuracy: `10/10` (100%)
- Top-3 accuracy: `10/10` (100%)
- Fully passing runs: `10/10`
- Qualitative review score: `60/60` (100%)

## postgres_down
Expected root cause: `PostgreSQL dependency outage`
- Run 1: PASS | top1=`PostgreSQL dependency outage` | trace=`traces/postgres_down-eval-001.json` | qualitative=`6/6`
- Run 2: PASS | top1=`PostgreSQL dependency outage` | trace=`traces/postgres_down-eval-002.json` | qualitative=`6/6`
- Run 3: PASS | top1=`PostgreSQL dependency outage` | trace=`traces/postgres_down-eval-003.json` | qualitative=`6/6`
- Run 4: PASS | top1=`PostgreSQL dependency outage` | trace=`traces/postgres_down-eval-004.json` | qualitative=`6/6`
- Run 5: PASS | top1=`PostgreSQL dependency outage` | trace=`traces/postgres_down-eval-005.json` | qualitative=`6/6`

## redis_down
Expected root cause: `Redis dependency outage`
- Run 1: PASS | top1=`Redis dependency outage` | trace=`traces/redis_down-eval-001.json` | qualitative=`6/6`
- Run 2: PASS | top1=`Redis dependency outage` | trace=`traces/redis_down-eval-002.json` | qualitative=`6/6`
- Run 3: PASS | top1=`Redis dependency outage` | trace=`traces/redis_down-eval-003.json` | qualitative=`6/6`
- Run 4: PASS | top1=`Redis dependency outage` | trace=`traces/redis_down-eval-004.json` | qualitative=`6/6`
- Run 5: PASS | top1=`Redis dependency outage` | trace=`traces/redis_down-eval-005.json` | qualitative=`6/6`

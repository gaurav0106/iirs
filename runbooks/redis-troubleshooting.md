# Redis troubleshooting

1. Confirm the Redis process is healthy and listening on port `6379`.
2. Check restart history, memory pressure, and network reachability from the affected service.
3. Correlate cache timeout and `connection refused` logs with cart or session latency spikes.
4. If Redis is unavailable, prepare a restart or failover step and require approval before executing it.
5. After mitigation, validate request latency, cache hit behavior, and service error rate.

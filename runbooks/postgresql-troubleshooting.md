# PostgreSQL troubleshooting

1. Confirm the PostgreSQL process is running and listening on the expected port.
2. Check recent restarts, readiness failures, and storage pressure before changing state.
3. Correlate application `connection refused` and timeout errors with database health.
4. If the database is down, prepare a restart or failover plan and get approval before executing it.
5. After recovery, validate application error rate, latency, and successful transactions.

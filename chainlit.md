# IIRS Demo

Use this Chainlit app to run the IIRS incident pipeline locally.

Try one of these inputs:

- `postgres_down`
- `redis_down`
- `catalogservice is timing out and PostgreSQL looks down`
- `basketservice cannot reach Redis and cart calls are failing`
- a JSON alert payload matching the fixture shape

The UI shows staged handoffs for:

- `Retriever`
- `Analyst`
- `Critic`
- `Planner`

After the incident brief appears, ask follow-up questions about root cause, evidence, or next actions.

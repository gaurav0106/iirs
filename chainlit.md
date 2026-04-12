# IIRS Demo

Use this Chainlit app to run the IIRS incident pipeline locally.

Try one of these inputs:

- `catalogservice is timing out and PostgreSQL looks down`
- `basketservice cannot reach Redis and cart calls are failing`
- `what broke in aspire shop right now?`
- `is everything healthy or broken right now?`
- `can you check the health of aspireshop?`
- `the aspire shop page is not loading at all`
- a JSON alert payload matching the fixture shape

The UI shows staged handoffs for:

- `Retriever`
- `Analyst`
- `Critic`
- `Planner`

Prompt routing rules:

- plain-English incident text becomes a new incident prompt
- broad breakage prompts route into live diagnosis
- broad health prompts route into a safer live health-check mode
- page/site-not-loading prompts bias toward `frontend`

After the incident brief appears, ask follow-up questions about root cause, evidence, runtime state, or next actions.

Good follow-ups:

- `why?`
- `show me more`
- `then what?`
- `is it healthy?`

Model-backed stages fail cleanly on timeout or invalid structured output instead of silently falling back to deterministic answers.

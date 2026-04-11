# IIRS Evaluation -- LLM-Backed Mode (gpt-5-mini)

- Generated at: `2026-04-11`
- Telemetry backend: `mock`
- Model: `gpt-5-mini` (reasoning effort: low, timeout: 120s)
- Total runs: `2` (1 per scenario)

## postgres_down
Expected root cause: `PostgreSQL dependency outage`
- Run 1: FAIL | top1=`PostgreSQL dependency outage` (correct) | qualitative=`5/6` (83%)
  - Missing action keywords: Inspect PostgreSQL health; Restart or fail over PostgreSQL
  - Missing action types: needs-approval
  - Qualitative gap: Open questions did not fully preserve critic's unresolved data gaps

## redis_down
Expected root cause: `Redis dependency outage`
- Run 1: FAIL | top1=`Redis dependency outage` (correct) | qualitative=`4/6` (67%)
  - Missing action keywords: Inspect Redis health; Restart or fail over Redis
  - Missing action types: needs-approval
  - Qualitative gaps: Safety boundary not explicit (0 needs-approval actions); Open questions dropped critic data gaps

## Analysis

The LLM achieves 100% top-1 root cause accuracy (matching deterministic mode) but underperforms on action plan structure:

| Metric | Deterministic | LLM-backed |
|--------|--------------|------------|
| Top-1 accuracy | 100% | 100% |
| Top-3 accuracy | 100% | 100% |
| Qualitative score | 100% (60/60) | 75% (9/12) |
| Action keyword match | 100% | 0% |
| Safety boundary explicit | 100% | 50% |

The LLM correctly identifies root causes but generates action plans with different phrasing than ground-truth keywords and sometimes omits the `needs-approval` action type boundary. This reflects a trade-off: model-generated plans are more natural but less predictable in structure. The deterministic path is the reliable baseline for evaluation; the LLM path adds flexibility for live, novel incidents.

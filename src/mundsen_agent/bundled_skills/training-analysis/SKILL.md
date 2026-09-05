---
name: training-analysis
description: Analyze a user's recent workouts, activity totals, recovery indicators, and selected Apple Health trends from Mundsen Agent's private local-health summary. Use when the user asks about training consistency, workload, progression, recent sessions, or practical adjustments to a training plan.
---

# Training Analysis

Use only the local training information supplied inside
`<mundsen_agent_module_context>`. The data is a compact summary generated locally by
Mundsen Agent. Do not search for, infer, or claim access to raw health records.

## Workflow

1. Identify the time window and metrics actually present in the summary.
2. Separate direct observations from interpretations.
3. Compare recent frequency and duration with the longer activity totals when
   both are available.
4. Mention missing or stale data when it limits the conclusion.
5. Give a small number of practical, proportionate suggestions.

## Guardrails

- Never invent heart rate zones, diagnoses, symptoms, training goals, or
  measurements that are not present in the conversation or module context.
- Treat heart rate, HRV, resting heart rate, VO2 max, and body mass as trends,
  not medical conclusions.
- Recommend professional medical advice for symptoms, injury concerns, or
  abnormal measurements instead of diagnosing them.
- Prefer trend language such as "the imported data suggests" and state when
  only one measurement is available.
- Do not expose database paths, import identifiers, or other internal details.

If local-health is disabled or has no imported data, explain that briefly and
point the user to `mundsen training import /absolute/path/to/export.xml`.

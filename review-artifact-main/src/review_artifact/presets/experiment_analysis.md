# Experiment Analysis Preset

Triage experiment or HPC job artifacts (meta.json, stderr, resource, status).

Focus on:
- measured vs expected outcomes
- timeout, killed, OOM, or queue/preemption signals
- bottlenecks and scaling anomalies
- whether the run completed successfully
- what to inspect or re-run next

This is the primary use case. Be concrete about evidence from meta/stderr/resource files.
Do not invent paths or line numbers not present in the collected bundle.

# Example: HPC job results layout

Typical directory structure after an HPC or benchmark run:

```text
results/latest/
├── meta.json       # job id, walltime, exit code
├── build.log       # compile/link output
├── stdout.txt      # program stdout
├── stderr.txt      # errors, timeout, OOM messages
├── resource.txt    # peak memory, CPU hours, I/O
└── status.txt      # completed / failed / killed
```

Configure with `examples/fugaku-results.toml` or:

```bash
review-artifact logs results/latest \
  --include meta.json \
  --include stderr.txt \
  --include resource.txt \
  --prompt experiment_analysis
```

Combined with run tracking tools:

```bash
runledger run ... && review-artifact logs runs/latest --backend llm
```

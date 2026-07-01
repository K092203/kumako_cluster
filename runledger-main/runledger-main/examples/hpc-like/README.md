# Example: hpc-like

Demonstrates `--launcher`, `ranks`, and `omp` without a real MPI stack. The
`fake-launcher.sh` script just echoes the rank/thread count and execs the
command; swap it for `mpirun -n {ranks}` or `srun` on a real cluster.

```bash
chmod +x fake-launcher.sh

runledger sweep configs.tsv \
  --launcher "./fake-launcher.sh {ranks}" \
  --budget 1 --elapse 30 --objective max-score \
  -- python3 ../hello/solver.py

runledger incumbent sweeps/latest --objective max-score
cat sweeps/latest/runs/000_rep001/stderr.txt   # see the launcher banner + #TUNE
```

`omp` is exported as `OMP_NUM_THREADS` for each run; on a real allocation the
anytime cutoff (`--elapse`) keeps the sweep inside your wall-time limit.

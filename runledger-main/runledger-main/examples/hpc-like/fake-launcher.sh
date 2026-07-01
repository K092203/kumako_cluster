#!/usr/bin/env bash
# A stand-in for mpirun/srun: prints the rank count, then execs the command.
# Lets you exercise --launcher "fake-launcher.sh {ranks}" without a real MPI.
set -euo pipefail
ranks="$1"; shift
echo "[fake-launcher] ranks=$ranks omp=${OMP_NUM_THREADS:-unset}" >&2
exec "$@"

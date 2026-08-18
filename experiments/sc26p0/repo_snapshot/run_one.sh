#!/bin/bash
# run_one.sh — 1 候補を走らせる。repo_snapshot/ へ置いて worker から呼ばれる。
#   引数: <ens> <cand> <dcost> <budget>
# ⚠️ A/B は runtime toggle のみ。ビルドは cluster_setup.json が 1 回だけ行う。
set -e
ENS=$1; CAND=$2; DCOST=$3; BUDGET=$4
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}
# 実行された実体の hash を残す (repo_snapshot_hash とは別物)
echo "#RUNMETA binary_hash=$(sha256sum ./solve | cut -c1-16) omp=${OMP_NUM_THREADS}" >&2
SC26_PROBE=1 SC26_CAND="$CAND" SC26_SEL=1 SC26_SDC="$DCOST" SC26_NSWAP=50 \
SC26_POSTKICK=0 SC26_PWARM=0.35 SC26_DECOMP=0.003 \
  ./solve "$ENS" "$BUDGET"

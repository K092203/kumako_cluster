#!/usr/bin/env bash
# Zip で配った先で最初に走らせる。ビルドして 1 本だけ流し、測定器が動くか見る。
set -euo pipefail
cd "$(dirname "$0")/repo_snapshot"
echo "== build =="
c++ -O2 -std=c++17 -fopenmp -DSC26_DEBUG -o solve sc26team.cpp
c++ -O2 -std=c++17 -o check_revised check_results_revised_0818.cpp
echo "solver  sha256: $(sha256sum solve        | cut -c1-16)"
echo "checker sha256: $(sha256sum check_revised| cut -c1-16)"
echo "== smoke (ens 4, cand 1..2, DCOST 12, 60s) =="
for c in 1 2; do
  out=$(OMP_NUM_THREADS=${OMP_NUM_THREADS:-4} bash run_one.sh 4 "$c" 12 60 2>&1)
  echo "cand=$c $(grep -oE 'sw_acc=[0-9]+' <<<"$out"|tail -1)" \
       "$(grep -oE ' E=[0-9.e+-]+' <<<"$out"|tail -1)" \
       "$(grep -oE 'Lc=[0-9.]+ Lc_lo=[0-9.]+ Lc_hi=[0-9.]+' <<<"$out"|tail -1)" \
       "$(grep -oE 'censored=[0-9]' <<<"$out"|tail -1)"
done
rm -f coord_*.txt
echo "== ok: censored=0 で Lc が候補ごとに違えば測定器は生きている =="

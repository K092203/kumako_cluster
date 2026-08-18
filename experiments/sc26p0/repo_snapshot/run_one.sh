#!/bin/bash
# run_one.sh — 1 候補を走らせる。repo_snapshot/ へ置いて worker から呼ばれる。
#   引数: <ens> <cand> <dcost> <budget>
# ⚠️ A/B は runtime toggle のみ。ビルドは cluster_setup.json が 1 回だけ行う。
set -e
ENS=$1; CAND=$2; DCOST=$3; BUDGET=$4
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}
# ⚠️ solver は入力を開けなくてもエラーにせず、そのまま壊れた計算を続けて exit 0 する。
#    (実際に踏んだ: sig_max=0 phi0=0 のまま進み最後に segfault した)
#    failed になるならまだしも、completed で嘘の数字が入るのが最悪なので手前で止める。
for f in ./solve "./input_${ENS}.txt"; do
  [ -e "$f" ] || { echo "#RUNERR missing=$f cwd=$(pwd)" >&2; exit 3; }
done
# ⚠️ ここが cand の唯一の権威ある記録。worker の result.json は job の seed を落とすので、
#    stderr に出さないと「どの候補だったか」が復元できない。
#    実行された実体の hash も残す (repo_snapshot_hash とは別物)。
CHECKER_HASH=$([ -x ./check_revised ] && sha256sum ./check_revised | cut -c1-16 || echo none)
echo "#RUNMETA version=1 ens=${ENS} cand=${CAND} dcost=${DCOST} budget=${BUDGET}" \
     "binary_hash=$(sha256sum ./solve | cut -c1-16) checker_hash=${CHECKER_HASH}" \
     "omp=${OMP_NUM_THREADS}" >&2
SC26_PROBE=1 SC26_CAND="$CAND" SC26_SEL=1 SC26_SDC="$DCOST" SC26_NSWAP=50 \
SC26_POSTKICK=0 SC26_PWARM=0.35 SC26_DECOMP=0.003 \
  ./solve "$ENS" "$BUDGET"

#!/usr/bin/env bash
# =====================================================================
# submit_seed_sweep.sh — Experiment A: seed 大量サンプル
#
#   目的: multi-start が有利になり得るほど seed 分布の裾が広いかを確認する。
#         現時点の富岳実測 (各3 seed) では分散が「L の離散格子1段分」しか
#         見えておらず、裾が未確認。ここを埋めるのが今夜の主目的。
#
#   ⚠️ solver の引数順は **./solve <ens> <budget> <seed> <perturb>**。
#      seed=0 は摂動なしの決定論的 baseline なので、分布サンプルには含めない
#      (このスクリプトは --start 1 から始める)。
#
#   ⚠️ perturb は今日の富岳実験と同じ 0.15 に固定する。勝手に変えない。
#      変えると富岳側の結果と比較できなくなる。
#
# 使い方:
#   experiments/sc26/submit_seed_sweep.sh <cluster_root> [seeds] [ens...]
#     例: experiments/sc26/submit_seed_sweep.sh /mnt/share/kumako 100 1 4 8
#
#   段階投入したい場合は seeds を小さくして複数回叩く
#   (--start は既存 job 数を見て自分でずらすこと)。
# =====================================================================
set -euo pipefail
ROOT="${1:?usage: submit_seed_sweep.sh <cluster_root> [seeds] [ens...]}"
SEEDS="${2:-100}"
shift 2 2>/dev/null || shift 1
ENS_LIST=("${@:-1 4 8}")
[ ${#ENS_LIST[@]} -eq 1 ] && read -r -a ENS_LIST <<< "${ENS_LIST[0]}"

PERTURB=0.15          # 富岳の今日の実験と同一。変更しないこと
DL=0.05               # 富岳実測で3問すべて最良だった値
BUDGETS=(50 100 200)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KUMAKO="$(cd "$SCRIPT_DIR/../.." && pwd)"

total=0
for ens in "${ENS_LIST[@]}"; do
  for B in "${BUDGETS[@]}"; do
    python3 "$KUMAKO/scripts/make_job.py" --root "$ROOT" \
      --prefix "sdA_e${ens}_b${B}_" --start 1 --count "$SEEDS" \
      --timeout-sec $((B + 120)) \
      --param "exp=seed_sweep" --param "ens=${ens}" --param "budget=${B}" \
      --param "dL=${DL}" --param "perturb=${PERTURB}" \
      -- "./solve" "${ens}" "${B}" "__seed__" "${PERTURB}" > /dev/null
    total=$((total + SEEDS))
    echo "  投入: ens=${ens} budget=${B}s seeds=1..${SEEDS}"
  done
done
echo "合計 ${total} ジョブを ${ROOT}/jobs/pending へ投入した"
echo "⚠️ SC26_DL0 は既定 (${DL}) を使う。solver の default が 0.05 なので環境変数は不要。"

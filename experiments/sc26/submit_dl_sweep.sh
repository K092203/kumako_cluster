#!/usr/bin/env bash
# =====================================================================
# submit_dl_sweep.sh — Experiment B: dL 細分化 (paired comparison)
#
#   目的: 富岳で dL=0.05 が3問すべてに勝ったが **各構成 n=1** だった。
#         統計的に再確認し、最適域を絞る。
#
#   ⚠️ **同一 seed 集合を全 dL で使う** (paired comparison)。
#      dL ごとに別 seed を使うと、差が dL によるものか seed によるものか
#      分離できなくなる。このスクリプトは全 dL で seed=1..N を使う。
#
#   dL は環境変数 SC26_DL0 で渡す。solver 側は SC26_DEBUG ビルドでのみ
#   この環境変数を読む (提出ビルドには残らない)。
#
#   ⚠️ make_job.py は command をそのまま実行するため、環境変数を前置する
#      には `env` 経由にする必要がある。
#
# 使い方:
#   experiments/sc26/submit_dl_sweep.sh <cluster_root> [seeds] [budget]
#     例: experiments/sc26/submit_dl_sweep.sh /mnt/share/kumako 20 100
# =====================================================================
set -euo pipefail
ROOT="${1:?usage: submit_dl_sweep.sh <cluster_root> [seeds] [budget]}"
SEEDS="${2:-20}"
BUDGET="${3:-100}"

PERTURB=0.15
DLS=(0.03 0.04 0.05 0.06 0.08)
ENS_LIST=(1 4 8)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KUMAKO="$(cd "$SCRIPT_DIR/../.." && pwd)"

total=0
for ens in "${ENS_LIST[@]}"; do
  for DL in "${DLS[@]}"; do
    tag="${DL//./}"
    python3 "$KUMAKO/scripts/make_job.py" --root "$ROOT" \
      --prefix "dlB_e${ens}_d${tag}_" --start 1 --count "$SEEDS" \
      --timeout-sec $((BUDGET + 120)) \
      --param "exp=dl_sweep" --param "ens=${ens}" --param "budget=${BUDGET}" \
      --param "dL=${DL}" --param "perturb=${PERTURB}" \
      -- env "SC26_DL0=${DL}" "./solve" "${ens}" "${BUDGET}" "__seed__" "${PERTURB}" > /dev/null
    total=$((total + SEEDS))
    echo "  投入: ens=${ens} dL=${DL} seeds=1..${SEEDS} budget=${BUDGET}s"
  done
done
echo "合計 ${total} ジョブを投入した (全 dL で同一 seed 集合 = paired)"

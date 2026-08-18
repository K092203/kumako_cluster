#!/usr/bin/env bash
# =====================================================================
# submit_calibration.sh — P1(時間軸の校正) と P2(worker 効果)を投げる
#
#   本体の seed 掃引より **先に** 通すこと。この2つが無いと、
#   得られた分布を解釈できない。
#
# ---------------------------------------------------------------------
# P1: 時間軸の校正
#   Kumako の 1 本が富岳の何秒相当かを知る。
#   seed=0 (摂動なし = 決定論的 baseline) を各 budget で1本ずつ走らせ、
#   富岳の同一条件 (seed=0 / dL=0.05) の best_L(t) 曲線と重ねる。
#   これが無いと Kumako の結果を富岳へ翻訳できない。
#
# P2: worker 効果 (★ 最大の方法論的リスク)
#   ソルバは wall-clock で打ち切るため、**遅い PC で走った run は
#   「悪い seed」と区別がつかない**。学校 PC の性能がばらつくと、
#   seed 分散のつもりで機械分散を測ってしまう。
#   同一 seed・同一 budget を全 worker で走らせ、出た差を機械差として
#   定量化する。この幅が seed 分散より大きければ、本体の解釈を
#   worker 別に分けるか、同一機種に絞る必要がある。
#
# 使い方:
#   experiments/sc26/submit_calibration.sh <cluster_root> [worker数]
#     worker数 … P2 で投げる本数。稼働 worker 数以上にすること
#                (少ないと一部の worker を測り逃す)。既定 32。
# =====================================================================
set -euo pipefail
ROOT="${1:?usage: submit_calibration.sh <cluster_root> [worker数]}"
NWORKER="${2:-32}"

PERTURB=0.15
DL=0.05
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KUMAKO="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== P1: 時間軸の校正 (ens1 / seed=0 / 各budget 1本) ==="
for B in 600 300 200 150 100 50 25; do
  python3 "$KUMAKO/scripts/make_job.py" --root "$ROOT" \
    --prefix "calP1_b${B}_" --start 0 --count 1 \
    --timeout-sec $((B + 120)) \
    --param "exp=calib_time" --param "ens=1" --param "budget=${B}" \
    --param "dL=${DL}" --param "perturb=0" \
    -- "./solve" 1 "${B}" 0 "${PERTURB}" > /dev/null
  echo "  投入: budget=${B}s seed=0"
done

echo
echo "=== P2: worker 効果 (ens1 / seed=1 固定 / budget=200 / ${NWORKER}本) ==="
echo "    同じ seed・同じ予算なので、出た差は**すべて機械差**"
python3 "$KUMAKO/scripts/make_job.py" --root "$ROOT" \
  --prefix "calP2_" --start 1 --count "$NWORKER" \
  --timeout-sec 320 \
  --param "exp=calib_worker" --param "ens=1" --param "budget=200" \
  --param "dL=${DL}" --param "perturb=${PERTURB}" \
  -- "./solve" 1 200 1 "${PERTURB}" > /dev/null
echo "  投入: ${NWORKER}本 (すべて seed=1)"

echo
echo "合計 $((7 + NWORKER)) ジョブ。所要 job-秒 ≈ $((600+300+200+150+100+50+25 + NWORKER*200))"
echo
echo "回収後にやること:"
echo "  1. P1 の L(budget) を、富岳の seed=0/dL=0.05 の best_L(t) と重ねて時間軸を校正"
echo "  2. P2 の L のばらつき幅を測る"
echo "     → この幅が seed 分散と同程度なら、本体の分析は worker 別に分けること"
echo "     experiments/sc26/analyze_seed.py が worker 情報を保持している"

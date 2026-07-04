# 模擬本選問題: 発電機配置の最適化

2025年本選課題(洋上風力タービン配置)を模した練習問題。
候補地50点から8点を選び、「基礎価値の合計 − 近接ペナルティ」を最大化する。
リハーサル(72時間通し)と、クラスタ全機能(setupフック・成果物回収・
sweep集計・探索ブリッジ)の end-to-end 検証に使う。

## 使い方

```bat
rem 1. ソルバーをスナップショットへ
copy examples\mock_problem\solver.py repo_snapshot\

rem 2. 単発で動作確認
python examples\mock_problem\solver.py --seed 1 --iters 2000

rem 3. 手動スイープ(パラメータ1組×5シード)
python scripts\make_job.py --count 5 --param iters=2000 --param t0=5.0 --param cooling=0.01 ^
  --artifact out/solution.txt --timeout-sec 60 ^
  -- python solver.py --seed __seed__ --iters __iters__ --t0 __t0__ --cooling __cooling__ --out out/solution.txt

rem 4. 集計
python scripts\summarize_results.py --by-sweep --agg min

rem 5. ベイズ最適化(ワーカー稼働中に)
python scripts\optuna_bridge.py --spec examples\mock_problem\search_spec.json --max-trials 200 --agg min

rem 6. 最良解の検証(提出前チェックの練習)
python examples\mock_problem\scorer.py --seed 1 --solution results\<worker>\<job>\artifacts\out\solution.txt
```

## リハーサルの流れ(本選前に1回)

1. 金曜夕方に「問題公開」と見なして docs/honsen_runbook.md の月曜チェックリストを実施
2. 問題到着→クラスタ稼働までの所要時間を計測(目標2時間)
3. 一晩探索を放置 → 翌朝 requeue_failed.bat --stale-running-sec 600 → summarize --by-sweep
4. scorer.py で最良解を検証(「富岳で朝検証」の代役)

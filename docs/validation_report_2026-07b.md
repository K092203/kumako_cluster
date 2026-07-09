# 改善案7件の実装・実証検証レポート 第2弾 (2026-07)

[validation_report_2026-07.md](validation_report_2026-07.md)(第1弾)と同じ方法論で、近年(2021〜2026)の
理論・手法から追加の改善候補7件を選定し、**このリポジトリの実コード**(`examples/mock_problem` の
焼きなましソルバー、`optuna_bridge.py` のエンジン実装)で検証した。全ての数値は
`experiments/results_*.json` に生データがあり、`experiments/validate_*.py` / `bench_collect.py` で再現できる。

- 検証環境: Linux (WSL2) / Python 3.12.3 (venv) / optuna 4.9.0 / numpy 2.5.1 / cmaes 0.13.0 / 20コア。
  ※リポジトリ本体は従来どおり Python 3.9+・標準ライブラリのみで動く(採用実装に新規依存なし)。
- 探索系検証 (V1,V2,V3) の目的関数: 第1弾と同一(mock_problem、iters上限600で飽和回避、シード平均、最大化)。
- 区間推定は全て paired bootstrap 95%CI。アーム間は同一repシードで対応付け。

## 結果一覧

| # | 内容 | 理論的出典 | 主要な実測値 | 判定 |
|---|---|---|---|---|
| V1 | Wilcoxonゲート付きシードレーシング (`--race`) | Optuna WilcoxonPruner (v3.6, 2024) / F-Race / SMAC3 (JMLR 2022) | 20シード同一予算で完走トライアル **+42%** (114 vs 80)・シード評価30%節約・誤枝刈り **0.0%**・best@50%予算 **+0.0149** (CI [+0.0046,+0.0267])・最終品質同等 (+0.003, CI跨ぎ)。5シードでは検定が発火せず無害 | **採用** (opt-in) |
| V2 | CMA-ES with Margin / LRA-CMA エンジン | Hamano+ GECCO 2022 / Nomura+ GECCO 2023, `cmaes` v0.13 | TPE推奨設定に対し best@20 **−0.070** (CI [−0.116,−0.025])、best@40 −0.044、best@60 −0.033 (全CIが負、fallback汚染なし)。lr_adapt 版も同傾向 | **不採用** (有意悪化) |
| V3 | ウォームスタート (`--warm-start-from`) | WS-CMA-ES (AAAI 2021) / Optuna enqueue 方式 | 分布シフト先 (別インスタンス集合) で best@5 **+0.248** (CI [+0.189,+0.312], 29勝1敗)、best@10 **+0.115** (CI [+0.083,+0.147], 28勝0敗)、best@30 で収束 (+0.004, CI跨ぎ)。rep間sd 0.175→0.037 | **採用** (opt-in) |
| V4 | プラトー型自動終了 (patience停止則) | Makarova+ (AutoML-Conf 2022) の stdlib 近似 | 200trial曲線へのオフライン適用で W50 でも loss平均 0.035 / p95 0.076 (基準 p95≤0.02 を大幅超過)。W20 は loss 0.121 | **不採用** (損失過大) |
| V5 | ストラグラー/孤児ジョブ hedging (`--hedge`) | The Tail at Scale (CACM 2013) / MapReduce backup tasks | 離散事象シム(280slot・8時間): ストラグラー率 0/2/5% で完了トライアル **+16.2% / +86.0% / +116.2%** (paired CI 全て正, 10/10勝)、p95レイテンシ 431→134s (2%時)、複製の無駄 3.6〜34% | **採用** (opt-in) |
| V6 | 結果ポーリングの索引化 (ResultIndex) | (工学項目) | poll あたり OS 操作 **9,880 → 23.6 (1/419)**、wall 441ms→2.4ms (蓄積3,000件時)。SMB の往復コスト直撃箇所 | **採用** (常時有効) |
| V7 | ジョブバッチング (複数シード/ジョブ) | (工学項目) | per-job オーバーヘッド実測: プロセス起動 ≈12.7ms + claim ≈0.045ms + snapshot判定 ≈1.26ms ≈ 30秒ジョブの **0.05%**。`#TUNE` 最終行のみ採用の仕様と衝突 | **不採用** (効果僅少・フォーマット破壊) |

検証前に制約で棄却したもの: **GPSampler / AutoSampler / Optuna Terminator**(いずれも torch 実行時必須をローカルで確認。
Terminator は optuna 4.9 で deprecated も確認)。第1弾で検証済みの HEBO(採用: 選択肢)・successive halving(不採用)は
スコープ外。V1 は第1弾で不採用になった successive halving の「統計ガード付き再挑戦」に相当する(下記詳細)。

## 詳細

### V1. レーシング — 「SHの誤淘汰」を統計検定で回避して予算だけ回収する

第1弾 improvement-3 の successive halving は 5 インスタンスで **−0.0424 の有意悪化**(誤淘汰が利得を食う)だった。
V1 は淘汰条件を「ランク上位でない」から「**incumbent より対応のある片側 Wilcoxon 検定で有意に悪い**
(p<0.05)かつ集計値も実際に悪い」に置き換え、生存者は必ず全シード評価する(kill 専用)。

- 検定は純Python実装(Pratt zero処理・mid-rank・n'≤25 は部分和DPによる厳密ヌル分布・以降は補正付き正規近似)。
  scipy 不要で builtin エンジンでも動く。
- 20シード・予算1600評価: race05 が完走トライアル 114.3 vs 80.0、シード評価30%節約、誤枝刈り0/30rep、
  best@800評価 +0.0149 (CI [+0.0046,+0.0267])、best@1600 +0.0029 (CI [−0.0142,+0.0201])。
  = **最終品質を落とさず、探索の anytime 性能と予算効率を改善**。
- 5シード・予算400評価: p=0.05・startup=ceil(5/3)=2 では有効対数が足りず検定が数学的に発火しない
  (n'=4 の片側最小p=0.0625)→ 完全な no-op(diff 0.0000)。SH で起きた悪化が構造的に起きない。
- p=0.10 版は節約40%・トライアル+69%だが誤枝刈り0.3%が出るため、既定は p=0.05。
- `--agg max` とは検定方向が不整合なので警告して無効化。`--agg min` は kill 専用として許可
  (mean方向で有意に劣る候補は min でもほぼ確実に劣る。生存者のシード数は削らないので min 推定は痩せない)。

### V2. CMA-ES — この探索条件では TPE 推奨設定に勝てない

`cmaes` パッケージ(numpy のみ)は torch 不要で HEBO 帯を狙える唯一の候補だったが、実測は全チェックポイントで
有意悪化(3次元 log 空間・60trial・バッチ8)。CmaEsSampler の independent fallback 警告は 0 件で、
純粋に CMA-ES vs TPE の比較になっている。サンプラー計算は 0.05s/rep と軽い(TPE 2.55s)が品質で不採用。
「TPE 頭打ち時の選択肢」枠は第1弾で検証済みの HEBO ブランチを維持する。

### V3. ウォームスタート — 「昨夜の study → 今夜の類似探索」の序盤を実測で加速

source(インスタンス 1..5)の study 上位5点を target(インスタンス 11..15 = 分布シフト)の探索へ
`enqueue` してから TPE を回す。best@5 +0.248 / best@10 +0.115(いずれも CI が 0 を跨がない)、30trial で
コールドに収束(害なし)。第1弾の πBO prior(専門家の勘)のデータ駆動版で、本選の
「ソルバーを直して探索を再開する」夜間サイクルに直接効く。
`--warm-start-from state/search/<前回>.best.json`(または builtin の `.history.jsonl`)で opt-in。
範囲外の数値は spec 境界へクリップ、choices 外の cat を含む候補は棄却、重複は排除。

### V4. 自動終了 — patience 則は本ワークロードでは損失が大きすぎる

TPE は 200 trial 目でも改善を出し続けるため、「W trial 改善なしで停止」はどの W でも停止率100%・
損失が採用基準(p95 ≤ 0.02)を大幅超過(W50 で p95 0.076)。regret ベースの理論版(Makarova+ 2022)は
GP 前提で torch 制約により持ち込めない。**探索の打ち切りは人間の判断に残す**のが現状の正解。

### V5. hedging — 「トライアル完了 = 最遅ジョブ律速」の構造的弱点を複製で潰す

トライアルは全シードの barrier 待ちなので、遅いPC/孤児ジョブ1本が 600 秒スケール(--stale-running-sec 相当)
の停滞を生む。残り2割以下・完了中央値×2+30s 超過のジョブへ同一シードの複製(`<job_id>-h1`)を投入し、
先着の result を採用する(ソルバーは同一シードで決定的なので測定整合)。
複製率のサーキットブレーカ(既定10%)で暴走を防ぐ。ストラグラー無し(0%)でも +16.2% は、
孤児(ワーカー喪失)の 600 秒 requeue 待ちを予防的に短絡する効果。既存の孤児回収(死んだPC の反応的回収)と
相補的に働く。注: 複製の結果も results/ に残るため `--by-sweep` の n がまれに +1 される(集計は影響軽微)。

### V6. ResultIndex — SMB ポーリングコストの支配項を除去

従来の `find_result` は poll ごと・in-flight ジョブごとに `results/*/<job_id>/result.json` を glob し、
蓄積結果数 R に比例して劣化(R=3,000 で 441ms/poll、SMB なら往復×9,880 で秒オーダー)。
`jobs/done`・`jobs/failed` の flat scandir(各1回)+ 新規分のみ find_result + メモ化に置換し、
**OS 操作 1/419・時間 1/180**。worker が result.json を書いてから done/ へ移動する順序に依存する設計で、
poll 時点の判定一致を全 poll で assert して検証済み。deadline 判定直前には従来方式のフォールバック走査を
入れて取り逃しを防ぐ。

### V7. バッチング — 実測でオーバーヘッドが無視できることを確認して却下

ジョブあたり固定費はプロセス起動 12.7ms が支配項で、本選想定の 30〜60 秒ジョブでは 0.02〜0.05%。
snapshot 差分同期(第1弾以前から実装済み)が SMB の主要コストを既に償却しており、バッチ化の上積みは
`#TUNE` 最終行仕様・タイムアウト粒度・per-seed 測定(--by-sweep/レーシング/hedging の前提)の破壊に見合わない。

## mainへの採用(実測に基づく)

1. **V6 ResultIndex** — 常時有効。挙動不変・poll コスト 1/419。
2. **V3 ウォームスタート** — `--warm-start-from`。序盤 +0.115@10trial (CI [+0.083,+0.147])。
3. **V1 レーシング** — `--race`(既定 p=0.05, startup=ceil(n/3))。多シード時に予算30%回収・誤枝刈り0。
   シード数が少ないと自然に無効化するため常用可能だが、第1弾 SH の教訓を尊重して opt-in。
4. **V5 hedging** — `--hedge`(既定 factor=2.0, 残り20%以下, ブレーカ10%)。夜間放置運用では付けることを推奨。
5. V2 CMA-ES / V4 自動終了 / V7 バッチング — 不採用(数値は上表)。コードは experiments/ に検証スクリプトとして残す。

## 再現方法

```
python3 -m venv .venv && .venv/bin/pip install pytest optuna numpy cmaes
.venv/bin/python experiments/validate_racing.py      # V1 (--quick あり)
.venv/bin/python experiments/validate_cmaes.py       # V2
.venv/bin/python experiments/validate_warmstart.py   # V3
.venv/bin/python experiments/validate_stopping.py    # V4
python3 experiments/validate_hedging.py              # V5 (stdlibのみ)
python3 experiments/bench_collect.py                 # V6 (stdlibのみ)
```

生データ: `experiments/results_racing.json` / `results_cmaes.json` / `results_warmstart.json` /
`results_stopping.json` / `results_hedging.json` / `results_collect_bench.json`

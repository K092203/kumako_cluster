# SuperCon2026 本選 — Kumako 実験セット (2026-08-17 夜)

学校 PC 群の Kumako Cluster で、本選ソルバの **seed 分散** と **dL 感度** の
統計を取るための一式。新しい分散基盤は作っていない。既存の
`make_job.py` / `worker.py` / `summarize_results.py` をそのまま使う。

---

## 何を決めるための実験か

> **同じ総計算資源を「1本の長時間探索」に使うべきか、「seed を変えた
> 複数 replica」に分散すべきか。**

判断に要るのは「時間による改善量」と「seed による分散」の比較。
前者は富岳で測った(A64FX でしか測れない)。**後者をここで測る。**

### 今日の富岳実測(前提として扱う)

| 項目 | 結果 |
|---|---|
| `dL` | 代表3問すべてで **0.05 が最良**(0.20 / 0.02 に勝つ)。⚠️ **各構成 n=1** |
| thread scaling | 2T→24T で **8.46倍**。以前の「4→8T で頭打ち」は**誤り**だった |
| seed 分散(300秒/24T、各3本) | 分散は **L の離散格子1段分**。最良 seed は無摂動 baseline と同値 |

⚠️ **未確認**: seed 分布の裾。n が少なすぎる。**ここを埋めるのが今夜の主目的。**

---

## 収録物

| ファイル | 役割 |
|---|---|
| `sc26team.cpp` | 本選ソルバ(final-prep の commit `09ec74e` 相当。`SOLVER_COMMIT.txt` 参照) |
| `sc26.h` | 公式ヘッダ。**改変禁止** |
| `input_1.txt` / `input_4.txt` / `input_8.txt` | 低・中・高多分散の代表3問 |
| `cluster_setup.json` | ビルド定義 |
| `submit_seed_sweep.sh` | Experiment A: seed 大量サンプル |
| `submit_dl_sweep.sh` | Experiment B: dL 細分化(paired) |
| `analyze_seed.py` | best-of-k の bootstrap 推定 |

⚠️ `repo_snapshot/` は `.gitignore` 対象なので、**この `experiments/sc26/` から
コピーして使う**(下の手順参照)。

---

## ソルバの仕様(実際にコードを読んで確認済み)

```
./solve <ens> <budget> <seed> <perturb>
```

⚠️ **引数順を間違えないこと。**

| 項目 | 値 |
|---|---|
| `DELTA_L_INIT`(圧縮の初期刻み) | **0.05**(富岳実測で決定) |
| escape(脱ジャミング) | **既定 OFF**(実測で貪欲に負けた) |
| `TRY_FRACTION`(試行時間上限) | **1.0 = 実質無効**(実測で退行と判明し撤去) |
| seed 摂動の既定振幅 | 0.15(単位は平均直径 ≈1.0) |
| **`seed=0`** | **摂動なし = 決定論的 baseline**。分布サンプルには含めない |

環境変数(`-DSC26_DEBUG` ビルドでのみ有効。提出ビルドには残らない):
`SC26_DL0` / `SC26_KICK` / `SC26_EXPAND` / `SC26_IDLE` / `SC26_TRYFRAC` / `SC26_ESCAPE`

### `#TUNE` 出力(stderr)

```
#TUNE elapsed=<秒> score=<-L> correct=1 L=<L> phi=<φ> ens=<番号> seed=<seed> perturb=<振幅> blocks=<出力回数> reject=<拒否回数>
```

- `score = -L`(大きいほど良い、という Kumako の慣習に合わせてある)
- **`reject > 0` は独立 validator が出力を拒否した = 異常**。
  `analyze_seed.py` は自動で除外し、除外内訳を表示する

---

## 学校 PC での手順

### 0. 取得

```bash
# clone 済みの場合
git fetch origin && git switch sc26-final-day1 && git pull

# 新規の場合
git clone -b sc26-final-day1 https://github.com/K092203/kumako_cluster.git
```

### 1. cluster root と repo_snapshot を用意

⚠️ **cluster root(全 worker から同じパスで見える共有)の場所はこのリポジトリからは
確定できない。環境に合わせて `ROOT` を決めること。推測で固定パスを書いていない。**

```bash
ROOT=<全workerから同じパスで見える共有ディレクトリ>
cd "$ROOT"

mkdir -p repo_snapshot
cp experiments/sc26/sc26team.cpp   repo_snapshot/
cp experiments/sc26/sc26.h         repo_snapshot/
cp experiments/sc26/input_*.txt    repo_snapshot/
cp experiments/sc26/cluster_setup.json repo_snapshot/
```

### 2. ビルドが通るか手元で1回確認

```bash
cd repo_snapshot && c++ -O2 -std=c++17 -fopenmp -DSC26_DEBUG -o solve sc26team.cpp && cd ..
```

通らなければ toolchain を確認する(`cluster_setup.json` の `setup_command` を
学校 PC の環境に合わせる。`c++` が無ければ `g++` 等)。

### 3. worker 起動

```bash
python3 scripts/supervise_slots.py --root "$ROOT" --slots <数> \
  --local-base /var/tmp/kumako-worker
python3 scripts/status.py --root "$ROOT"
```

⚠️ **`--slots` は控えめから始める。** 1 job あたり `OMP_NUM_THREADS` 本の
スレッドを使うので、`slots × threads` が物理コアを超えると
互いに食い合って測定が歪む。

### 4. ★ smoke test(必ず先に。いきなり数百本を投げない)

```bash
python3 scripts/make_job.py --root "$ROOT" --prefix "smoke_" --start 1 --count 2 \
  --timeout-sec 120 --param "ens=1" --param "budget=25" \
  -- "./solve" 1 25 "__seed__" 0.15
```

以下を**全部**確認してから本投入へ進む。

- [ ] ビルド成功(worker のログにエラーが無い)
- [ ] `results/<worker>/smoke_001/stderr.txt` に `#TUNE` が出ている
- [ ] `correct=1`
- [ ] **`reject=0`**
- [ ] `result.json` が生成されている
- [ ] job が `jobs/done/` へ移っている

### 5. Experiment A: seed 大量サンプル

```bash
experiments/sc26/submit_seed_sweep.sh "$ROOT" 100 1 4 8
# → 3 ens × 3 budget(50/100/200s) × 100 seeds = 900 jobs
```

段階投入したい場合は seeds を小さくして複数回叩く。

### 6. 監視・回収

```bash
python3 scripts/status.py --root "$ROOT"
python3 scripts/requeue_failed.py --root "$ROOT" --stale-running-sec 600
```

### 7. 集計

```bash
python3 scripts/summarize_results.py --root "$ROOT" --by-sweep --agg mean
experiments/sc26/analyze_seed.py --root "$ROOT" --json results_seed.json
```

`analyze_seed.py` が出すもの:

- ens × budget × dL ごとの median / IQR / best / worst
- **best-of-k(k = 1,2,3,4,6,8,12)の期待値・中央値・P10/P25/P75/P90**
- L の離散段ごとの出現頻度
- **次の L 段へ到達する確率 p と、理論値 `1-(1-p)^k`**

### 8. Experiment B: dL 細分化(A の投入後に)

```bash
experiments/sc26/submit_dl_sweep.sh "$ROOT" 20 100
# → 3 ens × 5 dL(0.03/0.04/0.05/0.06/0.08) × 20 seeds = 300 jobs
```

⚠️ **全 dL で同一 seed 集合(1..20)を使う paired comparison。**
dL ごとに別 seed を使うと、差が dL によるものか seed によるものか分離できない。

---

## 注意事項

- ⚠️ **Kumako の絶対速度を富岳へ外挿しないこと。** CPU アーキテクチャが違う。
  使ってよいのは分布形状・次段到達確率・best-of-k の**相対**比較のみ
- ⚠️ **`perturb=0.15` を変えないこと。** 今日の富岳実験と同じ値。変えると比較できない
- ⚠️ **`OMP_NUM_THREADS` を必ず記録すること。** core-second 比較と再現性に要る。
  worker 側で設定している値をメモに残す(`slots` と物理コア数も)
- ⚠️ `reject > 0` や `correct=0` を通常のスコアとして集計しない
  (`analyze_seed.py` は自動で除外するが、**除外件数が多いなら原因を調べること**)

## 未検証・推測のまま残っている事項

- 学校 PC の toolchain(`c++` か `g++` か、OpenMP が使えるか)— smoke test で確認
- 学校 PC の cluster root のパス — 環境依存なので固定していない
- 適切な `--slots` 数 — 物理コア数と `OMP_NUM_THREADS` から決める
- seed 分布の裾が本当に広いか(**これを測るのが今夜の目的**)
- `dL` 最適値の問題依存性(多分散度で変わるか)

## 明日の富岳側との接続

明朝、富岳から回収予定:

| 場所 | 内容 |
|---|---|
| `~/supercon2026/final-prep/r_*t48/log.txt` | 48T scaling(ens1/4/5/8、300秒) |
| `~/supercon2026/final-prep/f_1〜f_8/log.txt` | 24T・570秒 full(8問、`best_L(t)`) |

Kumako 側の `results_seed.json`(機械可読)と突き合わせて、
**24T vs 48T / 300→570秒の long-run 価値 / multi-start の価値 / dL 最適域**
を判断する。

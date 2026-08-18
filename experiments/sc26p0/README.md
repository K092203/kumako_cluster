# SC26 P0 — 準備候補の予測子検証(Kumako 研究セット)

## 何を確かめる実験か

> **安価な probe (`E_probe`) は、その候補の臨界サイズ `L_c` を予測できるか**

期待する符号は **`rho(E_probe, L_c) > 0`**(E が小さい候補ほど L_c も小さい。L は小さい方が良い)。

着想元は Kim & Hilgenfeldt (arXiv:2402.08390)。overjammed 準安定状態のエネルギーと
臨界充填率に強い負の相関があるという報告。

⚠️ **論文の φ 値を本課題へ転用しない。** 粒径分布・境界条件・変位制約が違う。
⚠️ **論文の E と `E_probe` を同一視しない。** 論文は十分緩和された準安定状態の
エネルギー、こちらは固定スイープ後の**安価な代理指標**。

---

## ⚠️ 先に読むべき前提(富岳で確定済み)

| 事実 | 内容 |
|---|---|
| **計算速度は律速ではない** | 時間+90% / スイープ+16% / 24→48スレッド のいずれでも最終 L が動かない |
| **swap50 の効果は撤回済み** | legacy 選択則では**実交換が全8問でゼロ**。A/B/C/D 4群対照でも全群一致 |
| **`NSWAP` は交換回数ではない** | 抽選試行回数。処置量は **`actual_sw_acc`** |
| **候補枯渇** | `sig_max` 依存の閾値が低多分散問題で候補集合を空にしていた → quantile 化で解消 |
| **同一 executable 原則** | `-Ofast` の再結合で、発火しないコードを足しただけでも L が格子1段ずれる |

**再実験しないもの**: legacy NSWAP sweep / 局所 random kick / failed-state swap rescue /
自己校正 give-up / 無条件の multi-start。新しい証拠がある場合のみ再検討する。

---

## 設計

```
worker.py (共有インフラ・変更しない)
   ↓ stderr.txt を全文保存
collect.py   #DIAGMETA / #DIAG / #SWAPDIAG / #PROBE / #PROBEEND / #TUNE を解釈
   ↓
records.jsonl (正本)  +  results.csv (派生物)
   ↓
analyze_p0.py  整合性検査 → instance ごとの Spearman → summary.md / summary.json
```

**raw log を不変の証拠として保存し、解釈は実験側の parser で発展させる。**
parser を直したら `stderr.txt` から何度でも再生成できる。

### hash の意味(混同しない)

| 名前 | 意味 |
|---|---|
| `git_commit` | 実験コードの版 |
| `repo_snapshot_hash` | 同期した snapshot の marker |
| **`binary_hash`** | **実際に走った solver バイナリの sha256** |
| `checker_hash` | 判定に使った checker バイナリの sha256 |

`binary_hash` / `checker_hash` / `cand` / `ens` / `dcost` は **`#RUNMETA` 行**、つまり
実際に走ったコマンド自身の出力から取る。⚠️ `worker.py` の `result.json` は job の
`seed` を書かないので、**`#RUNMETA` が無いと「どの候補だったか」を復元できない**。
投入時の params と食い違えば `meta_mismatch` に残る。

整合性検査は `analyze_p0.py` に統合してある(独立した `integrity.py` は作っていない)。

---

## 必要なもの

`bash` / `c++`(OpenMP対応) / `python3`(標準ライブラリのみ) / `coreutils`(`sha256sum` など)。
外部ライブラリは無い。

### 取得方法

```bash
git clone -b sc26-p0-swap-research https://github.com/K092203/kumako_cluster.git
```

GitHub の **Download ZIP でも動く**(`.git` が無くても `gen_configs.py` は落ちない)。
その場合 `git_commit` は `none(zip)` になり、実験の同一性は **`repo_snapshot_hash` と
`binary_hash`** が担保する。⚠️ どちらも実際の中身から計算するので、git より確実。

```bash
bash experiments/sc26p0/bootstrap.sh    # ビルド + 1本流して測定器の生存確認
```

## 使い方

### 1. cluster root を用意

```bash
ROOT=<全workerから同じパスで見える共有>
mkdir -p "$ROOT/repo_snapshot"
cp experiments/sc26p0/repo_snapshot/* "$ROOT/repo_snapshot/"
```

⚠️ ビルドは `cluster_setup.json` が **1 回だけ**行う。A/B は runtime toggle のみ。

### 2. worker 起動

```bash
python3 scripts/supervise_slots.py --root "$ROOT" --slots 14 --local-base /var/tmp/kumako-worker
python3 scripts/status.py --root "$ROOT"
```

### 3. Phase 0(smoke。必ず先に)

```bash
experiments/sc26p0/gen_configs.py --root "$ROOT" --phase 0     # 27 ジョブ
```

**L は見ない。** 見るのは次の 5 点。

- [ ] `result.json` と `stderr.txt` が生成される
- [ ] **candidate fingerprint が分岐している**(`fp` が候補ごとに違う)
- [ ] `actual_sw_acc` が `DCOST` に反応する
- [ ] `censored=0` で `Lc` が取れている
- [ ] `requested_nswap` と `actual_sw_acc` が別項目で残る
- [ ] `cand` が全レコードで埋まっている(`#RUNMETA` 由来)
- [ ] `binary_hash` が全レコードで一致している
- [ ] `meta_mismatch` が空(投入時の params と実行時の値が一致)

⚠️ **Phase 0 で `rho` を見ない。** 3 候補では `rho=+1.000` が偶然でも 1/6 で出る。
解析器は 1群 8 候補・4 群を満たすまで `insufficient` を返す。

あわせて壊し方の確認をする(1回ずつでよい)。

- [ ] worker を 1 台だけで動かす
- [ ] worker を複数台で動かして二重取得が起きない
- [ ] **running のまま止めて `requeue_failed.py` が拾い直す**
- [ ] 壊れた job JSON を置いて worker が落ちない
- [ ] 同じ job_id を二重投入して重複しない
- [ ] snapshot を書き換えて `binary_hash` 不一致が検出される

### 4. Phase 1(処置量の校正)

```bash
experiments/sc26p0/gen_configs.py --root "$ROOT" --phase 1 --cands 16
# 全8問 × 16候補 × DCOST{4,8,12,16} = 512 ジョブ
```

目的は **`actual_sw_acc` の反応曲線**。どの `DCOST` なら処置が入るかを決める。

⚠️ ローカル実測では **`DCOST=4` と `8` が完全同一**で、**12 で初めて効き始めた**。

### 5. Phase 2(P0 本番)

```bash
experiments/sc26p0/gen_configs.py --root "$ROOT" --phase 2 --dcost 12 --cands 32
```

### 6. 回収・解析

```bash
experiments/sc26p0/collect.py    --root "$ROOT"
experiments/sc26p0/analyze_p0.py --records "$ROOT/sc26p0_out/records.jsonl"
python3 scripts/requeue_failed.py --root "$ROOT" --stale-running-sec 900
```

---

## 停止条件(先に決めておく)

| 条件 | 判断 |
|---|---|
| `DCOST` を上げても `actual_sw_acc ≈ 0` | candidate rule 自体を見直す |
| `sw_acc` は増えるが変位急増・緩和失敗が増える | 処置量過多 |
| 候補分岐も `Lc` 分解能も十分で `rho ≈ 0` | **`E_probe` predictor を棄却する** |

⚠️ **「論文で効いたから」で positive に解釈しない。**

⚠️ **per-job の `sw_acc=0` は異常ではない**(本当に受理されない場合がある)。
**全条件・全候補で 0** なら instrumentation の異常を疑う。

---

## runtime toggle

| 環境変数 | 意味 |
|---|---|
| `SC26_PROBE` | 1 で候補評価モード |
| `SC26_CAND` | 候補番号(probe 用 RNG のみを変える) |
| `SC26_SEL` | 0=legacy(固定比率) 1=quantile(順位) |
| `SC26_SDC` | swap の変位コスト上限 |
| `SC26_NSWAP` | 抽選試行回数(**交換回数ではない**) |
| `SC26_SWMODE` | 0=実交換 1=抽選のみ 2=抽選もしない(4群対照) |
| `SC26_DECOMP` / `SC26_REFINE` / `SC26_EVALSEC` | `L_c` 探索の粗い刻み / 二分回数 / 1評価の秒数 |

⚠️ probe 用 RNG (`g_prng`) は Golden の RNG (`g_rng`) と**完全に分離**されている。
`SC26_PROBE=0` なら研究機能を足す前と挙動が一致する。

---

## `L_c` の測り方

以前は「前の L の状態を引き継いで少しずつ広げる」実装で、**経路依存**のため刻みを
変えると比較できなかった。さらに分解能不足で 6 件中 5 件が同値になった。

現在は **毎回 probe 状態から直接その L へアフィン展開して緩和する**。各評価の起点が
同じなので、粗い走査 → 区間の二分、が正当化できる。

```
Lc / Lc_lo / Lc_hi / Lc_res / censored
```

を保存する。`censored` は `0`=取得成功 / `1`=上限到達 / `2`=予算切れ。
⚠️ **`censored != 0` は順位相関へ入れない。**

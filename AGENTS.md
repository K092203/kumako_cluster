# AGENTS.md — このクラスタを動かす AI への指示書

> このファイルは **Claude Code**(`CLAUDE.md` はこのファイルへのシンボリックリンク)と
> **Codex CLI** がセッション冒頭で読む唯一の指示書。
> ここは **SuperCon2026 本選の研究専用ブランチ** `sc26-p0-swap-research`。
> 実験の詳細な操作は `experiments/sc26p0/README.md`。**作業前に必ず両方読むこと。**

## 応答は日本語

コード・コマンド・API 名のみ英語可。

---

## 何をしている実験か

SuperCon2026 本選の課題は **2次元多分散円盤パッキング**(N=3000 / 周期境界 / L を最小化 /
平均変位 ≤ 10)。8 問あり、富岳で **600 秒**を 8 問へ配分して解く。

このクラスタ(**20台 × 14スロット = 280 並列**)は富岳ではない。ここでやるのは
**富岳の時間を使わずに済ませられる研究**、具体的には P0:

> **安価な probe (`E_probe`) は、その候補の臨界サイズ `L_c` を予測できるか**

期待する符号は **`rho(E_probe, L_c) > 0`**。着想元は Kim & Hilgenfeldt (arXiv:2402.08390)。

⚠️ **論文の φ 値も E の定義もそのまま転用しない。** 粒径分布も境界条件も違うし、
`E_probe` は論文の準安定エネルギーではなく**固定スイープ後の安価な代理指標**。

---

## ⚠️ 絶対に守ること

1. **`scripts/worker.py` など共有インフラを変更しない。** ここは他の用途でも使う土台。
   実験は `experiments/sc26p0/` と `repo_snapshot/` の中だけで完結させる。
2. **A/B は runtime toggle だけで行う(同一 executable 原則)。**
   `-Ofast` の再結合により、**発火しないコードを足しただけで最終 L が格子1段ずれる**。
   ソースを変えて比べた「改善」は改善ではない。実測で踏んだ罠。
3. **checker を絶対に `-Ofast` でビルドしない。** 採点は `-O2` 相当で、
   **ペアごとに `overlap < 1e-10`** を見る(平均ではない)。
4. **compiler luck を探さない。** dummy code や配置を変えて良い軌道を引く行為は禁止。
5. **検証していない変更を投入経路へ入れない。** 280 並列は速いが、
   壊れた測定器で埋めた records は全部ゴミになる。
6. **富岳には入らない。** 手元のファイルだけ読む。
7. 外部 AI(Codex CLI / OpenCode)は **read-only のセカンドオピニオン専用**。
   API (`ask-ai.sh`) は使わない。

---

## ⚠️ 今いちばん重要な未解決事項

**warmup が wall-clock 依存で、paired 実験が成立していない。**

```cpp
const double warm = PROBE_WARM * budget;
while (sc26_elapsed_seconds() < warm ...) { ... relax(nL, warm) ... }
```

候補は別マシン・別スロットで走るので、CPU の速さと負荷で `bestL` が変わる。
つまり**候補ごとに probe 開始状態が違う**。`E_probe` の差が swap の効果なのか
開始状態の差なのか分離できない。

**→ Phase 0(測定器の動作確認)は現行設定で回してよい。**
**→ Phase 1 の大量投入は、これを直すまでやらない。**

直し方は `experiments/sc26p0/README.md` の「未解決の設計欠陥」に書いてある。
本命は **ens ごとの共通 prepared state**(決定的 warmup → hexfloat で保存 → 全 worker で読む)。
実装は書いたが**決定性の実証が済んでいないので入れていない**。
最低限でも `bestL` / `phi` / `warm_steps` / `state_fp` を記録し、
**開始状態が違う候補を同じ相関解析へ混ぜない**こと。

---

## 手順

```bash
# $ROOT = 全PCが同じ共有ツリーを指す場所。リポジトリ全体をここに置く
cd "$ROOT"
python3 -V                                    # ⚠️ 3.9 以上でなければ worker が起動しない
python experiments/sc26p0/bootstrap.py        # ビルド + 2本流して測定器の生存確認

python3 experiments/sc26p0/gen_configs.py --root "$ROOT" --phase 0
python3 scripts/supervise_slots.py --root "$ROOT" --slots 14 --local-base C:\\supercon-worker
python3 scripts/status.py --root "$ROOT"

python3 experiments/sc26p0/collect.py    --root "$ROOT"
python3 experiments/sc26p0/analyze_p0.py --records "$ROOT/sc26p0_out/records.jsonl"
```

### Phase 0 で見るもの(**L は見ない**)

`fp` が候補ごとに分岐 / `Lc` が候補ごとに違う / `actual_sw_acc` が `DCOST` に反応 /
`censored=0` / `cand` が全件埋まる / `binary_hash` 一致 / `meta_mismatch` 空。

⚠️ **Phase 0 で `rho` を見ない。** 3 候補なら `rho=+1.000` は偶然でも 1/6 で出る。
解析器は 1群 8 候補・4 群を満たすまで `insufficient` を返す。**この閾値を緩めない。**

### Phase 1 の `DCOST` は `rho` で選ばない

`actual_sw_acc` が十分発火する / `max_trial_disp` が壊れない / relax 失敗が急増しない、
の3つだけで固定する。「相関が一番きれいな条件」を選ぶと Phase 2 が確認実験でなくなる。

---

## 詰まったときの確認順序

| 症状 | 見るところ |
|---|---|
| setup が全滅 | `$ROOT/tools/w64devkit` が無い(**gitignore なので clone には入らない**) |
| worker が起動しない | `python -V` が 3.9 未満 / `$ROOT` にリポジトリ全体が無い / `$ROOT` 以外の cwd から実行した |
| pending が減らない | 各PCの `$ROOT` が同じ共有実体か / 共有先に作成・rename 権限があるか |
| 全ジョブ failed | `logs/` と `--local-base` の `setup.log`。`repo_snapshot` が空 / `c++` に OpenMP が無い / local-base が noexec |
| `#RUNERR missing=` | snapshot に入力か `solve` が無い。**壊れた数字を records に入れないための正しい停止** |
| `E_probe ≈ 1e-9` | budget 不足。probe が完全に解けている。解析器は `probe浅い` として弾く |
| `Lc` が候補間で同値 | 分解能不足。`SC26_REFINE` を上げる |

---

## 再実験しないもの(`docs/decisions.md` 相当)

新しい証拠なしに再提案すると時間が溶ける。

- **swap50 の効果は撤回済み**。legacy 選択則では**実交換が全8問でゼロ**(`sw_acc=0`)。
  A/B/C/D の4群対照でも全群一致した。RNG 消費が軌道を変えるという代替仮説も棄却済み。
- **`NSWAP` は交換回数ではない**(抽選試行回数)。処置量は **`actual_sw_acc`**。
- **計算資源は律速ではない**。時間 +90% / スイープ +16% / スレッド 24→48、いずれも ΔL=0。
- **変位制約は効いていない**(`max_trial_disp=2.89` に対し上限 10)。
- 却下済み: local kick 救出 / 失敗状態 swap / multi-start / 自己校正型見切り /
  時間上限 / `TRY_FRACTION=0.10`(L が悪化した) / escape(候補3。前提が誤読だった)。

⚠️ **AI の一般論より自分たちの実測を上位に置く。**
⚠️ **1 問だけの測定を信用しない。** 1問で良く見えて 8問中 7問で悪化した例がある。

---

## 記録の作法

- `stderr.txt` を**不変の証拠**として残す。解釈は `collect.py` に置く。
  parser を直せば過去ログから何度でも再生成できる。
- `records.jsonl` が正本、`results.csv` は派生物。
- hash の意味を混同しない: `git_commit`(実験コードの版) /
  `repo_snapshot_hash`(配った中身) / **`binary_hash`(実際に走った実体)** /
  `checker_hash`(判定に使った実体)。
- **`cand` は `#RUNMETA` 行だけが真値**。`worker.py` の `result.json` は job の
  `seed` を書かないので、これが無いと「どの候補だったか」を復元できない。

## 判定の作法

- `censored != 0` は順位相関へ入れない。
- 候補が分岐していない群・probe が浅い群は診断失敗として除外する。
- **`sw_acc=0` は per-job なら異常ではない**。全条件・全候補でゼロなら計測系を疑う。
- 「論文で効いたから」で positive に解釈しない。
  分岐も分解能も十分でそれでも `rho ≈ 0` なら、**`E_probe` predictor を棄却する**。

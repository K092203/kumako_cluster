> 🌐 [English](README.md) · [日本語](README.ja.md) · [中文](README.zh.md)

# runledger

**締切のある CLI 実験のための、軽量な「実行台帳(run ledger)」。**
任意コマンドの実行を完全な snapshot として残し、**anytime 打切り**付きで構成を掃引し、
**いつでも提出できる現時点ベスト(incumbent)** を必ず確保します。重厚な実験基盤は不要です。

```text
測る -> 残す -> 比べる -> ベストを保つ（いつ落ちても出せる形で）
```

- **ステータス:** v0.1.0(プレリリース)· MIT · 標準ライブラリのみ、実行時の外部依存ゼロ
- **検証:** Python 3.12 / Linux (WSL2) 上でテスト35件 green(2026-06-30)— [検証結果](#検証結果)参照
- **既知の制約:** Windows 実機は未検証([既知の制約](#既知の制約)参照)

---

## なぜ作るか

その場しのぎの実験ログは消えてしまい、ジョブが途中で殺されると最良の結果も失われます。
`runledger` はすべての実行を後から検死可能にし(stdout / stderr / env / git 状態 /
input ハッシュ / リソース使用量)、**現時点ベストを常に** `state/incumbent.json` に保持します。
掃引の途中でプリエンプトされても、提出できる有効な結果が必ず手元に残ります。

## 既存ツールとの違い

| 比較対象 | runledger が足すもの |
|---|---|
| **Guild AI / DVC exp** | 締切下の **anytime 打切り** と **提出可能な `incumbent.json`**。「全 trial を回して後で最良を選ぶ」モデルではない。 |
| **hyperfine** | 単なる計時ではなく、構成 **掃引** + 完全な **snapshot** + **incumbent** までを一連にする。 |
| **Optuna / GPTune / OpenTuner** | runledger は **optimizer ではない**。LHS/grid/manual で十分。高度な探索は `configs.tsv` を吐く上流に逃がす。 |

**主対象(wedge):** 締切の中で任意コマンドの実験を何度も回し、途中で殺されても直近ベストを
提出できる形で持っておきたい人。HPC ジョブ投入者、競技/最適化ソルバ開発者、ベンチ掃引を回す人
— ML 実験トラッキングではなく、もっと低レイヤの「締切付き任意コマンド実験」が中心。

## 3つの保証

1. **Anytime** — 各構成の実行前に、残り時間で終わらないと判断したら綺麗に停止し、
   そこまでに測れた分をすべて残す。
2. **提出可能な incumbent** — `state/incumbent.json` をライブ更新し、**correct** かつ
   厳密に良い結果のときだけ前進させる。
3. **非ゼロ終了は計測値として絶対に信用しない** — crash や timeout が「良い結果」に
   化けることはない。

3点とも[検証結果](#検証結果)で実証しています。

## インストール

```bash
cd runledger
pip install -e ".[dev]"
```

Python 3.11 以上が必要。実行時の外部依存はありません(pytest は開発用のみ)。

## クイックスタート

```bash
# 1. 単発実行を snapshot として残す
runledger run --name baseline --timeout 30 -- python3 examples/hello/solver.py --alpha 1.5 --budget 0.1

# 2. 探索空間から構成を生成（ラテン超方格。--seed で再現可能）
runledger gen-configs examples/hello/solver.space.tsv --n 8 --seed 1 -o configs.tsv

# 3. anytime 打切り付きで掃引。incumbent はライブ更新
runledger sweep configs.tsv --budget 1 --elapse 30 --objective max-score -- python3 examples/hello/solver.py

# 4. 完了した掃引から incumbent を（再）計算
runledger incumbent sweeps/latest --objective max-score

# 5. run / sweep の要約
runledger summary runs/latest
runledger summary sweeps/latest
```

## コマンド

```bash
runledger run -- <cmd>               # 1コマンドを実行して snapshot 化
runledger gen-configs space.tsv      # space.tsv -> configs.tsv (lhs|grid)
runledger sweep configs.tsv -- <cmd> # anytime 掃引: results.csv + ライブ incumbent
runledger incumbent <sweep-dir>      # objective に対する最良の correct 構成
runledger summary <run|sweep dir>    # 人間向けの要約
```

objective: `max-score`, `min-elapsed`, `score-per-sec`。

## ディレクトリ構成

```text
runs/<timestamp>-<name>/   meta.json argv.txt stdout.txt stderr.txt env.txt status.txt resource.json input.sha256
sweeps/<round>/            configs.tsv results.csv errors.log runs/<id>_rep<NNN>/
state/incumbent.json       いつでも提出できる現時点ベスト
```

`latest` は symlink(不可な環境では `latest.txt` にフォールバック)。
スキーマの詳細は [docs/snapshot-format.md](docs/snapshot-format.md) と
[docs/sweep-format.md](docs/sweep-format.md) を参照。

## 計測の取り込みは pluggable（`--measure`）

プログラムは好きな形で結果を出力し、runledger は measure spec でそれを読み取ります。
**どの方式でも、非ゼロ終了は信用しません。**

| kind | 取り込み方 |
|------|-----------|
| `tune-line`（既定） | stderr 末尾の `#TUNE elapsed=.. score=.. correct=..` 行 |
| `regex` | stdout/stderr へのパターン（`[measure]` テーブルで設定） |
| `json:PATH` | JSON ファイル `{"elapsed":..,"score":..,"correct":..}` |
| `file:PATH` | 1行目を score とする素朴なファイル |
| `none` | exit/wall/outcome のみ |

`#TUNE` は設定不要の既定であって、**強制ではありません**。詳細は
[docs/measure-spec.md](docs/measure-spec.md)。

## 検証結果

**Python 3.12.3 / Linux 6.18 (WSL2) / 2026-06-30** で取得。

### テストスイート — 35件 green

```text
tests/test_cli.py          3   （異常系の綺麗な処理: 不正な --measure / --objective）
tests/test_gen_configs.py  6   （space 解析, LHS/grid, pow2 丸め, 重複除去）
tests/test_incumbent.py    6   （objective, correct のみ, 厳密改善, 無回帰）
tests/test_measure.py      9   （tune-line/regex/json/file/none, 非ゼロは不信用）
tests/test_run.py          7   （成功/失敗/timeout/missing-bin, input ハッシュ, env allowlist）
tests/test_sweep.py        4   （results 追記, 失敗継続, anytime, 重複id）
                          ---
                           35   passed in 0.74s
```

### 振る舞いの確認（エンドツーエンド）

| 振る舞い | コマンド | 観測結果 |
|---|---|---|
| snapshot + 計測 | `runledger run -- … solver.py` | `completed exit=0`、tune-line で計測抽出、`runs/latest/` に7ファイル生成 |
| 再現可能な掃引 | `gen-configs --seed 1` → `sweep` | incumbent が `id=003 score≈1.808` に収束、5構成中4回ライブ更新 |
| **anytime 打切り** | `sweep --budget 100 --elapse 0.001` | `configs run: 0/1 (stopped early)`、`errors.log`: *anytime cutoff before id 000* |
| **非ゼロは不信用** | `#TUNE …` を出してから `exit 3` | `outcome=failed`、`measure: elapsed=None score=None correct=None` |
| **異常系の綺麗な処理** | `--measure bogus` / `--objective bogus` | `error: unknown …` + `exit=2`、traceback なし、1件も実行しない |
| `--bindir` でコンパイル済み C++ | `make` → `gen-configs --bin solver` → `sweep --bindir build` | g++ 13.3.0 でビルド、`8/8` 構成実行、`argv.txt` が `build/solver` に解決、incumbent `id=000 score=1.95` |

代表的な出力:

```console
$ runledger sweep configs.tsv --budget 1 --elapse 30 --objective max-score -- python3 solver.py
sweep: sweeps/round-001
configs run: 5/5  incumbent updates: 4

$ runledger sweep one.tsv --budget 100 --elapse 0.001 -- python3 solver.py
sweep: sweeps/round-002
configs run: 0/1  incumbent updates: 0  (stopped early)
# errors.log: anytime cutoff before id 000: spent=0.0s est=100.0s elapse=0.0s

$ runledger run -- python3 -c "import sys; print('#TUNE score=999 correct=1', file=sys.stderr); sys.exit(3)"
failed  exit=3  wall=0.013s
measure[tune-line]: elapsed=None score=None correct=None    # 非ゼロ終了なので #TUNE を無視
```

コンパイル済みの [examples/cpp-solver](examples/cpp-solver) も、ビルドから掃引まで
エンドツーエンドで実行確認済み(g++ 13.3.0):

```console
$ make && runledger gen-configs solver.space.tsv --n 8 --seed 1 --bin solver -o configs.tsv
$ runledger sweep configs.tsv --bindir build --budget 1 --elapse 30 --objective max-score
sweep: sweeps/round-001
configs run: 8/8  incumbent updates: 1
$ cat sweeps/latest/runs/000_rep001/argv.txt        # bin 列からバイナリを解決
build/solver
--alpha
1.949822
--budget
0.075639
$ runledger incumbent sweeps/latest --objective max-score
incumbent updated: state/incumbent.json
  id=000 objective=max-score score=1.949822 elapsed=0.075639
```

CI は Python テストスイートを Python 3.11 / 3.12 / 3.13 で実行します。

## セキュリティ

runledger は env(既定は allowlist)、コマンドライン、stdout/stderr、input ハッシュ、
git 状態を記録します。全 env の記録は `--capture-env=all` 指定時のみ、input 内容のコピーは
`--copy-input` 指定時のみで、秘密らしきファイルは除外されます。詳細は
[docs/security.md](docs/security.md)。

## ドキュメント

- [docs/snapshot-format.md](docs/snapshot-format.md) — run ディレクトリ + meta.json
- [docs/sweep-format.md](docs/sweep-format.md) — configs.tsv, results.csv, incumbent.json, anytime
- [docs/measure-spec.md](docs/measure-spec.md) — 計測方式
- [docs/hpc.md](docs/hpc.md) — launcher, ranks/omp, スケジューラ注記
- [docs/security.md](docs/security.md) — env/秘密情報の扱い

例: [examples/hello](examples/hello)（純 Python）、
[examples/cpp-solver](examples/cpp-solver)（コンパイル + `--bindir`）、
[examples/hpc-like](examples/hpc-like)（`--launcher`, ranks/omp）。

## review-artifact との組み合わせ

[review-artifact](https://github.com/K092203/review-artifact) は runledger が生成した
snapshot を read-only の AI で triage します:

```bash
runledger run -- ./solver < input.txt
review-artifact logs runs/latest      # snapshot を read-only の AI で triage
```

## 既知の制約

- **Windows 実機は未検証。** symlink → `latest.txt` フォールバックと空の `resource.json`
  経路は実装済みですが Windows 上で未テストのため、当面は best-effort 扱い。
- `resource.json` は best-effort: Unix では `getrusage` の差分、それ以外は `{}`。
  `maxrss` は high-water mark であり、厳密な per-run 値ではありません。
- `gen-configs --method grid` は float 次元を粗く離散化(lo/mid/hi)。主役は LHS。

## 非目標（v0.1）

スケジューラ submit アダプタ(PJM/SLURM/PBS)、web UI、SQLite インデックス、
クラウド同期、optimizer / ベイズ探索、GitHub bot。

## ライセンス

MIT — [LICENSE](LICENSE) を参照。

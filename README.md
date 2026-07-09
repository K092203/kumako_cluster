# kumako_cluster — SuperCon ローカル探索クラスタ

学校PC室のPC20台を、共有フォルダ(SMB)だけで束ねる疎結合ジョブクラスタです。
サーバプロセス・DB・SSH・管理者権限を一切必要とせず、各PCが共有フォルダを
ポーリングして自律的にジョブを取得・実行します。

**目的は巨大なMPIクラスタを作ることではなく、独立した多数の実験(パラメータ探索・
複数シードでの評価)を20台へ分配して高速に回すこと**です。SuperCon2026 本選
(2026-08-17〜21、富岳)で、富岳が使えない夜間の探索環境として使います。

- 設計思想と内部構造 → [docs/architecture.md](docs/architecture.md)
- ジョブ・結果JSONの仕様 → [docs/job-format.md](docs/job-format.md)
- 本選当日の運用手順 → [docs/honsen_runbook.md](docs/honsen_runbook.md)
- パラメータ探索の使い方 → 下記「パラメータ探索」+ [docs/offline_optuna.md](docs/offline_optuna.md)
- クラスタの方針・大会規則対応 → [supercon_cluster_policy.md](supercon_cluster_policy.md)

---

## 全体像

```
[親機 ×1]                                  [子機(Worker) ×20]
 make_job.py      ジョブJSONを投入 ───┐        register_worker.py  番号を取得
 optuna_bridge.py 探索してジョブ投入 ─┤        supervise_slots.py  Nスロットを監視・再起動
 status.py        状態を眺める          │       worker.py ×Nスロット   ジョブを取得・実行
 summarize_results.py 結果を集計 ───┐  │        launcher_agent.py  親機の開始命令を待つ
 requeue_failed.py 失敗/孤児を再投入 │  │
 archive_results.py 結果を退避       │  │
 bench_throughput.py スループット測定 │  ▼
        ┌────────────────────── SMB 共有フォルダ ──────────────────────┐
        │ jobs/pending/  投入済みの待機ジョブ                          │
        │ jobs/running/  claim 済みの実行中ジョブ(job--worker.json)   │
        │ jobs/done/     正常終了                                       │
        │ jobs/failed/   失敗・タイムアウト・setup失敗                  │
        │ results/<worker>/<job>/  stdout/stderr/result.json/artifacts/ │
        │ status/        各ワーカーの死活(idle/running/stopped/stale)  │
        │ control/       stop_all 等の命令ファイル                      │
        │ repo_snapshot/ ワーカーへ配る実行コード(git管理外)          │
        │ state/         incumbent.json / search/(探索の途中経過)      │
        │ workers/       worker番号の登録台帳(git管理外)              │
        │ tools/         w64devkit 等のポータブルツール(git管理外)    │
        └──────────────────────────────────────────────────────────────┘
```

- **pull型**: 子機が共有フォルダを監視して自分でジョブを取得。親機から子機へログインしない
- **排他制御**: `os.replace` による `pending/ → running/` のアトミックな移動でジョブを claim。ロックサーバもDBも不要
- **実行の局所化**: `repo_snapshot/` を各PCのローカルディスク(既定 `C:\supercon-worker\`)へ同期してから実行。SMB上でのコード実行を避ける
- **測定の信頼性ゲート**: exit code 0 かつ非タイムアウトのときだけ `#TUNE` 行の値を採用。異常終了時は measure を null 化

詳細は [docs/architecture.md](docs/architecture.md) を参照してください。

---

## セットアップ

前提: 全PC(親機・子機)に **Python 3.9 以上**。満たさない場合、各スクリプトは
起動時に明示エラーで止まります(`scripts/_pyversion.py`)。

### 子機(各PCで一度だけ)

PCごとに安定した worker 番号(worker01〜worker20)を割り当てます。

```bat
setup_worker_auto.bat
```

これは `register_worker.py` を呼び、`workers/<id>.json` に登録して、その番号を
ローカル(`C:\supercon-worker\worker_id.txt`)に記録します。以後、同じPCは
同じ番号を再利用します。

> **注意**: `worker.py` は worker 番号が未指定だとエラー終了します(以前の
> `worker01` 既定へのフォールバックは廃止)。必ず `setup_worker_auto.bat` 系や
> `--worker-id` を経由して番号を確定させてください。

### ツールチェーン(C/C++ソルバーを使う場合)

管理者権限なしで動く w64devkit を `tools/w64devkit/` に配置します。手順は
[tools/README.md](tools/README.md)。配置後、1台で動作確認:

```bat
verify_toolchain.bat
```

### 探索エンジン(パラメータ探索を使う場合)

親機に Optuna をオフライン導入します。手順は [docs/offline_optuna.md](docs/offline_optuna.md)。
導入できなくても、`optuna_bridge.py` は標準ライブラリのみのフォールバックエンジンで動きます。

---

## ワーカーの起動

推奨は **supervisor 方式**。1台につき N スロットを監視し、落ちたスロットを自動で
起動し直します。

```bat
start_worker_supervisor.bat 14        rem このPCで14スロットを起動・監視
```

スロット数を固定せず、完了レートと失敗率(タイムアウト)から自動増減させたい場合は
`--adapt` を付けます(実行中ジョブを失わずグレースフルに縮退。詳細と実測は
[docs/validation_report_2026-07.md](docs/validation_report_2026-07.md))。

```bat
start_worker_supervisor.bat 14 adapt   rem このPCで14スロット + 自動増減
rem または直接: python scripts\supervise_slots.py --slots 14 --adapt --min-slots 2 --max-slots 14
```

親機から全PCへまとめて開始命令を出す場合(各PCで `start_launcher_agent.bat` を
起動しておく):

```bat
start_launcher_agent.bat              rem 子機: 命令待ち
start_all_slots.bat 14                rem 親機: 全launcherへ「14スロット起動」を指示
start_all_slots.bat 14 adapt          rem 同上 + 自動増減(第2引数 adapt を付けるだけ)
```

スロット数(14=物理コア / 20=論理コア)は実測で決めます。手順は
[docs/honsen_runbook.md](docs/honsen_runbook.md) の「スロット数ベンチ手順」。

---

## ジョブ投入

### ダミー / ストレスジョブ

```bat
make_job.bat --count 100                              rem 軽いダミージョブ100件
make_stress_jobs.bat 5000 60                          rem CPU負荷ジョブ(5000件・各60秒)
```

`make_stress_jobs.bat` は `templates/stress_solver.py` を `repo_snapshot/` へ
自動コピーしてから投入します。

### 任意コマンドのジョブ

`--` の後ろが各ワーカーで実行されるコマンドです。

```bat
make_job.bat --count 280 --timeout-sec 60 -- python solver.py --seed __seed__
```

- コマンドはリスト形式(`-- python solver.py ...`)推奨。文字列にすると `shell=True` で
  実行され、`%SUPERCON_ROOT%` などの環境変数が展開されます(トレードオフあり、
  [supercon_cluster_policy.md](supercon_cluster_policy.md) 参照)
- `__seed__` などの `__key__` はジョブのフィールド値・`params` の値に置換されます

### 成果物(解ファイル)の回収

`--artifact` で指定したファイル(ジョブ cwd 相対の glob)を
`results/<worker>/<job>/artifacts/` へ回収します。

```bat
make_job.bat --count 5 --artifact out/solution.txt -- python solver.py --seed __seed__ --out out/solution.txt
```

### パラメータ付きジョブ(手動スイープ)

`--param k=v` は `params` に格納され、`__k__` テンプレートで使え、同じ params を
持つジョブは同一 `sweep_id`(paramsのハッシュ)でまとめられます。

```bat
make_job.bat --count 5 --param alpha=0.5 --param beta=2 --artifact out/sol.txt ^
  -- python solver.py --alpha __alpha__ --beta __beta__ --seed __seed__ --out out/sol.txt
```

ジョブJSONの全フィールドは [docs/job-format.md](docs/job-format.md) を参照。

---

## C/C++ ソルバー(ビルドフック)

`repo_snapshot/cluster_setup.json` を置くと、各ワーカーがスナップショットを
ローカルへ取り込んだ直後に **1回だけ** ビルドを実行します(内容が変わると自動で
再ビルド)。

```json
{
  "setup_command": "\"%SUPERCON_ROOT%\\tools\\w64devkit\\bin\\g++.exe\" -O2 -o solver.exe solver.cpp",
  "timeout_sec": 300
}
```

ビルドログは各PCの `<local-dir>\setup.log`。詳細は [tools/README.md](tools/README.md)。

---

## パラメータ探索(Optunaブリッジ)

親機で探索ドライバを回すと、`ask → pending投入 → result回収 → tell` のループで
クラスタ全体をベイズ最適化の実行部として使えます。1トライアル=全インスタンス
(シード)ぶんのジョブで、集計値(平均/最悪)を最適化します。

```bat
python scripts\optuna_bridge.py --spec examples\mock_problem\search_spec.json ^
  --max-trials 500 --parallel 32 --agg min
```

- `--agg min` は「最悪シードでも良い」パラメータを選ぶ(過学習対策)
- TPE は既定で **推奨設定**(`multivariate` + `constant_liar`)で動く。実測で
  従来既定より収束が有意に良い([docs/validation_report_2026-07.md](docs/validation_report_2026-07.md))。
  従来動作に戻すなら `--tpe-profile default`
- 事前知識(「このパラメータはこの辺が良い」)があれば spec の各 param に
  `prior` を書くと序盤の探索が加速する(下記スペック参照。opt-in)
- Optuna 未導入なら `--engine builtin`(ランダム+山登り、標準ライブラリのみ)
- Ctrl+C や `control/stop_all` で安全停止。再実行で途中から再開(結果は再利用)
- 進捗は `status/bridge-<name>.json`、最良は `state/search/<name>.best.json`

スペックの書き方は [templates/search_spec.example.json](templates/search_spec.example.json)、
探索の全体像は [docs/architecture.md](docs/architecture.md) の「7. パラメータ探索ブリッジ」節。

---

## 監視・集計・保守

```bat
status.bat                                    rem 各ワーカーの状態(60秒無更新でstale表示)
summarize.bat                                 rem 結果一覧+ベスト(--update-incumbent付き)
summarize.bat --by-sweep --agg min            rem パラメータセット単位で集計(mean/iqm/min/max)
rem   --by-sweep の表には層化ブートストラップ95%CI列が付く。CIが重なる2案は
rem   「そのシード数では優劣を判定できない」を意味する(rliable, Agarwal et al. 2021)
requeue_failed.bat                            rem failed/ を pending/ へ戻す
requeue_failed.bat --stale-running-sec 600    rem 加えて、落ちたワーカーの孤児ジョブを回収
python scripts\archive_results.py             rem done/failed/results を archive/<日付>/ へ退避
python scripts\bench_throughput.py --minutes 10 --count 2000   rem スループット測定
```

### 全停止と再開

```bat
type nul > control\stop_all                   rem 全ワーカー・全ブリッジを安全停止
del control\stop_all                          rem 停止解除(その後 supervisor を再起動)
```

コマンドの早見表は [docs/honsen_runbook.md](docs/honsen_runbook.md) の「コマンド早見表」節。

---

## solver に守ってほしい規約

- 標準出力または標準エラーに次の行を出すと集計対象になります(最後に出た行を採用):

  ```text
  #TUNE elapsed=12.34 score=98765 correct=1
  ```

  `score` は最適化対象、`correct=1/true/yes/ok` で妥当性、`elapsed` は参考値。
  この値が採用されるのは exit code 0 かつ非タイムアウトのときだけです。
- 解ファイルは `--artifact` で指定した相対パスに書き出す(ジョブごとに事前クリアされます)
- ソルバーは1スレッドで書く。並列度はスロット数で稼ぐ設計のため、ワーカーは
  `OMP_NUM_THREADS=1` 等を既定で注入します(ジョブの `env` で上書き可)

---

## フォルダ構成

| パス | 内容 | git |
|---|---|---|
| `scripts/` | 親機・ワーカー用スクリプト | ✓ |
| `templates/` | ソルバー雛形・cluster_setup例・探索スペック例 | ✓ |
| `examples/mock_problem/` | 練習用の模擬本選問題(生成+スコアラ+ソルバー) | ✓ |
| `docs/` | 設計・仕様・運用ドキュメント | ✓ |
| `tests/` | pytest(claim排他・分類・requeue・setup・artifacts・sweep・探索・動的スロット) | ✓ |
| `.github/workflows/` | GitHub Actions(push/PRで pytest を自動実行) | ✓ |
| `*.bat` | 親機・子機の起動ラッパー | ✓ |
| `repo_snapshot/` | ワーカーへ配る実行コード(本選ソルバーを置く) | ✗ |
| `jobs/` `results/` `status/` `control/` `state/` | 実行時の状態 | 一部✗ |
| `workers/` | worker番号の登録台帳(実機名・利用者を含む) | ✗ |
| `tools/` | ポータブルツールチェーン・wheel群 | ✗(READMEのみ) |

`repo_snapshot/`・`workers/`・`tools/`(READMEを除く)・実行時ディレクトリは
`.gitignore` 済みです。実行ログ・ベンチ結果・生成物は長期保存しません
(必要ならジョブを再投入して作り直します)。

`runledger-main/` と `review-artifact-main/` は設計参考用で、通常の実行には使いません。

---

## テスト

```bash
python -m pytest tests/ -q
```

push / PR ごとに GitHub Actions([.github/workflows/tests.yml](.github/workflows/tests.yml))が
同じテストを Python 3.9 / 3.12 で自動実行します。Optuna を入れない構成でも
フォールバックエンジンが動くことを別ジョブで確認しています。

Windows 実機がなくても、一時ディレクトリを root にしてワーカーやブリッジを直接
起動すれば end-to-end 検証ができます(bat はレビューのみ)。

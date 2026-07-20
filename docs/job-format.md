# ジョブ・結果・制御ファイルの仕様

クラスタ内でやり取りされる JSON の全フィールドをまとめる。手で JSON を書くとき、
結果を機械処理するとき、ソルバーを連携させるときの参照。設計背景は
[architecture.md](architecture.md)。

---

## ジョブ JSON(`jobs/pending/<job_id>.json`)

`make_job.py` が生成し、`worker.py` が消費する。手書きも可。

### 必須・基本フィールド

| フィールド | 型 | 意味 |
|---|---|---|
| `job_id` | string | ジョブ識別子。ファイル名や結果ディレクトリ名に使われる |
| `command` | string \| list \| `"dummy"` | 実行コマンド。`"dummy"`/空 は擬似ジョブ(sleepしてスコアを返す) |
| `timeout_sec` | number | 実行タイムアウト秒(別名 `time_limit_sec`、既定30) |

### 実行を制御するフィールド

| フィールド | 型 | 意味 |
|---|---|---|
| `cwd` | string | 作業ディレクトリ(ローカル `repo/` からの相対) |
| `env` | object | 追加環境変数。`OMP_NUM_THREADS` 等の既定を上書きできる |
| `artifacts` | list\<string\> | 回収するファイルの glob(cwd相対)。実行前にクリア→実行後に収集 |
| `params` | object | ソルバーパラメータ。`__key__` テンプレートで展開され、集計キーになる |
| `sweep_id` | string | パラメータセットの識別子(既定 params のハッシュ) |

### ダミージョブ用フィールド

| フィールド | 型 | 意味 |
|---|---|---|
| `seed` | int | シード。`__seed__` に展開。ダミーのスコア計算にも使う |
| `score` | number | ダミーが返すスコア(既定 `1000 + seed`) |
| `elapsed` | number | ダミーが sleep する秒数(既定 0.01、最大60) |

### テンプレート展開

`command` 内の `__key__` は、ジョブのトップレベル値(dict/list を除く)と `params` の
値に置換される。例: `params={"alpha":0.5}`, `seed=3` のとき
`--alpha __alpha__ --seed __seed__` → `--alpha 0.5 --seed 3`。
`{key}` 形式(Python format)は**展開されない**(`__key__` のみ)。

### 例

```json
{
  "job_id": "opt042",
  "command": ["solver.exe", "--alpha", "__alpha__", "--seed", "__seed__", "--out", "out/sol.txt"],
  "cwd": ".",
  "timeout_sec": 60,
  "params": {"alpha": 0.5, "iters": 2000},
  "sweep_id": "3f1c9a0b2d4e",
  "artifacts": ["out/sol.txt"],
  "env": {"OMP_NUM_THREADS": "1"}
}
```

---

## 結果 `result.json`(`results/<worker>/<job_id>/result.json`)

`worker.py` が各ジョブの終了時に書く。集計はこのファイルだけで完結する。

| フィールド | 型 | 意味 |
|---|---|---|
| `job_id` | string | ジョブ識別子 |
| `worker` | string | 実行したワーカー(スロット)ID |
| `sweep_id` | string \| null | ジョブの sweep_id |
| `params` | object \| null | ジョブの params |
| `outcome` | `completed`\|`timeout`\|`failed` | 判定結果(§測定値ゲート) |
| `exit_code` | int \| null | プロセス終了コード(タイムアウト時 null) |
| `wall_elapsed` | number | ワーカー実測の経過秒(参考。富岳とは相関しない) |
| `measure` | object | `{elapsed, score, correct}`。ゲート非通過時は全て null |
| `warnings` | list<string> (optional) | 測定値について注意がある場合の警告。`correct` 未報告で既定の合格扱いになった場合に記録される |
| `artifacts` | object | `{collected: [...], missing: [...]}` |
| `error` | string | ワーカー内部エラー/ setup失敗のメッセージ(なければ空) |
| `finished_at` | string | ISO8601 終了時刻 |

`measure.score` が最適化対象、`measure.correct` が妥当性(false なら候補から除外)。`correct`
が未報告のときは null のまま既定で合格扱いとなり、信頼済みの測定値であれば `warnings` に記録される。

### 同じディレクトリの他ファイル

| ファイル | 内容 |
|---|---|
| `stdout.txt` / `stderr.txt` | ソルバーの出力 |
| `status.txt` | outcome を1行で |
| `meta.json` | `{job, worker, started_at, finished_at}`(ジョブ全体の記録) |
| `artifacts/` | 回収した解ファイル(相対パス構造を保持) |

---

## ステータス `status/<worker>.json`

ワーカーが自分の死活を書く。`status.py` が読んで一覧表示する。

| フィールド | 型 | 意味 |
|---|---|---|
| `worker` | string | ワーカーID |
| `status` | `idle`\|`running`\|`stopped` | 状態(`status.py` は60秒無更新で `stale` と表示) |
| `current_job` | string \| null | 実行中のジョブID |
| `message` | string | 補足メッセージ |
| `updated_at` | string | ISO8601。孤児回収・stale判定はこれを見る |

supervisor は `<id>-supervisor.json`、launcher は `<id>-launcher.json`、探索ブリッジは
`bridge-<name>.json` を同形式で書く。

---

## ビルドフック `repo_snapshot/cluster_setup.json`

ワーカーがスナップショット取込直後に1回だけ実行するビルド定義。

| フィールド | 型 | 意味 |
|---|---|---|
| `setup_command` | string \| list | ビルドコマンド。文字列なら shell 経由で `%SUPERCON_ROOT%` 展開可 |
| `timeout_sec` | number | ビルドのタイムアウト(既定600) |

成功時のみマーカーを記録し、以後スナップショットが変わるまで再実行しない。

---

## 探索スペック(`optuna_bridge.py --spec`)

[templates/search_spec.example.json](../templates/search_spec.example.json) 参照。

| フィールド | 型 | 意味 |
|---|---|---|
| `name` | string | 探索名(状態ファイル名の接頭辞。既定はファイル名) |
| `params` | object | パラメータ定義(下表) |
| `command` | list | ソルバーコマンド。`__key__`・`__seed__` を含める |
| `instances` | list\<int\> | 1トライアルで評価するシード群 |
| `timeout_sec` | number | 各ジョブのタイムアウト(既定60) |
| `cwd` / `env` / `artifacts` | — | ジョブへそのまま渡される(任意) |

### パラメータ定義

| type | 追加キー | 説明 |
|---|---|---|
| `float` | `low`, `high`, `log?` | 連続値。`log:true` で対数一様 |
| `int` | `low`, `high`, `log?` | 整数値 |
| `cat` | `choices` | カテゴリ(非空リスト) |

任意で各 param に **事前分布** `prior: {center, confidence}` を付けられる(πBO 方式)。
`center` は「だいたいこの辺が良い」という中心値(数値は範囲内、cat は choices のいずれか)、
`confidence` は 0〜1 の強さ。書いた場合、序盤の一定割合が prior 近傍からサンプルされる
(`optuna_bridge.py --no-prior` で無効化)。書かなければ従来どおり範囲一様。

```json
{
  "name": "mock-placement",
  "params": {
    "iters": {"type": "int", "low": 200, "high": 20000, "log": true},
    "t0": {"type": "float", "low": 0.1, "high": 50.0, "log": true, "prior": {"center": 5.0, "confidence": 0.7}},
    "strategy": {"type": "cat", "choices": ["greedy", "anneal"]}
  },
  "command": ["python", "solver.py", "--seed", "__seed__", "--iters", "__iters__", "--out", "out/solution.txt"],
  "instances": [1, 2, 3, 4, 5],
  "timeout_sec": 60,
  "artifacts": ["out/solution.txt"]
}
```

---

## `state/incumbent.json`

`summarize_results.py --update-incumbent` が書く現職(最良)。モードで内容が変わる。

**ジョブ単位(既定)**:

```json
{"objective": "max-score", "job_id": "...", "worker": "...",
 "score": 98765, "elapsed": 12.3, "result_path": "...", "finished_at": "..."}
```

**sweep単位(`--by-sweep`)**:

```json
{"mode": "sweep", "objective": "max-score", "agg": "mean",
 "sweep_id": "...", "params": {...}, "score": 74.3, "ci95": [73.9, 74.6], "n": 5, "ok": 5}
```

`agg` は `mean`(既定)/`iqm`/`min`/`max`。`ci95` は層化ブートストラップ95%信頼区間
(値が少なく層が退化する場合は `[null, null]`)。

探索ブリッジの最良は別途 `state/search/<name>.best.json` に随時保存される。

---

## 制御ファイル(`control/`)

| ファイル | 効果 |
|---|---|
| `stop_all` | 全ワーカー・supervisor・launcher・ブリッジを安全停止 |
| `<worker_id>.stop` | 指定ワーカーを停止 |
| `<base_id>.slots.stop` | 指定PCの supervisor を停止 |
| `<base_id>.launcher.stop` | 指定PCの launcher を停止 |
| `<base_id>.start_slots.json` / `start_slots_all.json` | launcher へスロット起動を指示。最低限 `{"slots": N}`。`adapt` を含めると動的スロットを有効化: `{"slots": N, "adapt": true, "min_slots": 2, "max_slots": 14, "adapt_interval_sec": 60}`(min/max/interval は省略可) |

stop 系は中身より**存在**が意味を持つ。start_slots 系のみ中身を読み、`slots` と
(あれば)`adapt`・`min_slots`・`max_slots`・`adapt_interval_sec` を supervisor に渡す。
内容が変わると launcher は supervisor を起動し直す(同じファイルの上書きでも反映)。

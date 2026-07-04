# アーキテクチャ

kumako_cluster の内部構造と設計判断の根拠をまとめる。運用手順は
[honsen_runbook.md](honsen_runbook.md)、JSONの仕様は [job-format.md](job-format.md) を参照。

---

## 1. 設計制約(なぜこの形なのか)

学校PC室という環境が方式を決めている。

| 制約 | 帰結 |
|---|---|
| 管理者権限なし | サービス常駐・FWのinbound開放・ソフトのインストール不可 |
| SSH不可 | 親機から子機へリモート実行・デプロイできない |
| 確実に使える経路はSMB共有フォルダのみ | 通信・調整はすべて共有フォルダ上のファイルで行う |
| 一般ユーザーで Python 実行は可 | ランタイムは Python 標準ライブラリ中心 |
| 探索用途・時間計測はしない | 実行時間の精密測定は捨て、スコアと正しさだけを収穫する |

この制約下で「サーバプロセスを立てず・子機へログインせず・ロック機構なしで」
多数の独立ジョブを安全に分配できる方式が、**共有フォルダ上のファイルキュー**である。

代替案(親機にHTTP API / MPI等)を採らなかった理由は
[supercon_cluster_policy.md](../supercon_cluster_policy.md) の設計判断節を参照。

---

## 2. ファイルキューと排他制御

ジョブは1件=1個のJSONファイル。状態はディレクトリで表す。

```
jobs/pending/   投入済み・未取得
jobs/running/   取得済み・実行中     ファイル名: <job_id>--<worker_id>.json
jobs/done/      正常終了
jobs/failed/    失敗・タイムアウト・setup失敗
```

### claim(取得)のアトミック性

ワーカーは `pending/` のファイルを1つ選び、`running/<job_id>--<worker_id>.json` へ
`os.replace()` で移動する([worker.py](../scripts/worker.py) `claim_job`)。

- `os.replace` は同一ファイルシステム上で**アトミック**。2台が同じファイルを同時に
  取りに行っても、成功するのは1台だけで、もう1台は `FileNotFoundError`(=次の候補へ)
- ロックサーバもDBも不要。これが本方式の核心

### thundering herd の緩和

最大280〜400スロットが一斉にポーリングすると、`sorted()` の先頭ファイルに claim が
殺到して SMB 上で無駄な失敗が増える。候補リストを `random.shuffle` してから claim を
試すことで衝突を散らす。結果として **ジョブの処理順は投入順(ID順)ではなくなる**。
全件完走が目的なので実害はない。

### 完了時の移動と job_id 衝突回避

終了したジョブは `done/` または `failed/` へ `os.replace`。同名 job_id が既に
あれば `-2`, `-3`, … と採番して衝突を避ける(`unique_path`)。

---

## 3. ワーカーのライフサイクル

[worker.py](../scripts/worker.py) `main` → `process_job` の流れ:

```
起動 → worker_id 確定(--worker-id / 環境変数 / worker_local/worker_id.txt)
      ※未確定ならエラー終了(worker01 への暗黙フォールバックは廃止)
ループ:
  control/stop_all か <id>.stop があれば "stopped" を書いて終了
  claim_job で pending から1件取得
    取れなければ "idle" を書いて poll-sec 待って再試行(--once なら終了)
  process_job:
    1. copy_repo_snapshot: repo_snapshot をローカルへ差分同期(必要ならビルド)
    2. status を "running" に更新
    3. 実行前に artifact のマッチを削除(前ジョブの残骸混入を防ぐ)
    4. run_command: Popen で起動し、30秒ごとにハートビート(status更新)
    5. artifact を results/<worker>/<job>/artifacts/ へ回収
    6. 測定値ゲート → outcome 判定 → stdout/stderr/status/meta/result を書き出し
    7. done/ or failed/ へ移動
  status を "idle" に戻す
```

### ハートビート

実行が長いと status が更新されず、[status.py](../scripts/status.py) が `stale`(既定
60秒無更新)と誤表示する。`run_command` は `subprocess.Popen` + ポーリングにして、
30秒ごとに `update_status(..., "running", job_id)` を呼ぶ。これで status は常に真実を映す。
タイムアウトは `time.monotonic()` の締切で管理し、超過時は `kill()` して回収する。

### スナップショット差分同期とビルドフック

`copy_repo_snapshot` は `repo_snapshot/` 内の全ファイルの**相対パス+サイズ+mtime**
から SHA-256 ダイジェストを計算し、ローカルの `repo.sha256` マーカーと一致すれば
コピーを**スキップ**する。毎ジョブの rmtree→copytree による SMB 帯域の浪費を
避けるためで、短時間ジョブを大量に流すほど効く。

判定は `stat()` のみで行い、**ファイル内容は読まない**(内容ハッシュにすると、
この判定自体が毎ジョブ・全スロットでスナップショット全体を SMB 越しに読み直す
ボトルネックになるため)。トレードオフとして「同サイズ・同mtimeのまま内容だけ
変わった」場合は検知しないが、通常の上書き保存では mtime が変わるので実用上問題ない。
確実に再配布したいときはスナップショット内の任意のファイル(例: `version.txt`)を
更新すればよい。

内容が変わった(または初回の)ときだけ再コピーし、`repo_snapshot/cluster_setup.json`
があれば `setup_command` をローカルの `repo/` で実行する(C/C++のビルド等)。
**成功したときだけ** マーカーを書くので、ビルド失敗時は次ジョブで再試行される。

> **副作用**: ローカルの `repo/` はジョブ間で使い回される。ソルバーが `repo/` 内に
> 一時ファイルを書くと次のジョブに残る。解ファイルは `--artifact` で宣言すれば
> 実行前に自動クリアされるので混入しない。

### 環境変数の注入(`command_env`)

setup・全ジョブに以下を注入する:

- `SUPERCON_ROOT`(共有クラスタルート)/ `SUPERCON_WORKER_ID` / `SUPERCON_JOB_ID`
- `OMP_NUM_THREADS=1` ほか BLAS/OpenMP系(§3.3「1スレッド原則」の強制。ジョブの
  `env` で上書き可)

文字列コマンドは `shell=True` で実行されるため `%SUPERCON_ROOT%\tools\...` の展開が効く。
リスト形式は shell を経由しないので展開されない。

---

## 4. 測定値の信頼性ゲート

`#TUNE elapsed=.. score=.. correct=..` 行を stdout/stderr の**末尾から**探す
([worker.py](../scripts/worker.py) `parse_tune`)。ただし採用するのは
`exit_code == 0 かつ 非タイムアウト かつ ワーカー内部エラーなし` のときだけ。
それ以外では measure を `{"elapsed": null, "score": null, "correct": null}` に落とす。

outcome は次のいずれか:

| outcome | 条件 | 移動先 |
|---|---|---|
| `completed` | 上記ゲートを通過 | `done/` |
| `timeout` | 実行時間が timeout_sec 超過 | `failed/` |
| `failed` | 非ゼロ終了・setup失敗・例外 | `failed/` |

これにより「途中で壊れた計算のスコアが最良と誤認される」経路を塞ぐ。

---

## 5. 障害復旧

3層で「ジョブが黙って消える」を防ぐ。

1. **supervisor**: [supervise_slots.py](../scripts/supervise_slots.py) が各スロットの
   プロセスを監視し、落ちていれば再起動する(PCが生きている限りスロットは復活)
2. **孤児ジョブ回収**: PCごと落ちると `running/` にジョブが取り残される。
   `requeue_failed.py --stale-running-sec N` が、`running/` 内で mtime が N 秒より古く、
   かつ**担当ワーカーの status も stale(または欠損)**なジョブだけを `pending/` へ戻す。
   生きているワーカーの実行中ジョブは回収しない(status の updated_at で判定)
3. **失敗再投入**: `requeue_failed.py` が `failed/` を `pending/` へ戻す(手動トリガ)

`control/stop_all` を置けば全ワーカー・全ブリッジが安全に停止する。

---

## 6. 結果の集計

[summarize_results.py](../scripts/summarize_results.py) は `results/*/*/result.json`
だけを読む(meta.json 不要)。2つのモード:

- **ジョブ単位(既定)**: 各ジョブを一覧し、候補(completed かつ correct≠false かつ
  score≠null)からベストを選ぶ
- **sweep単位(`--by-sweep`)**: `sweep_id` ごとに n / ok / mean / min / max を集計。
  `--agg mean|min|max` でランキング基準を選ぶ

`--update-incumbent` で `state/incumbent.json` を更新する(mode により内容が変わる。
[job-format.md](job-format.md#stateincumbentjson) 参照)。

### なぜ sweep 単位が要るか

スコア型課題は隠し評価インスタンスへの過学習が起きる。1つのパラメータセットを
複数シードで評価し、`--agg min`(最悪値)で比べれば「たまたま1シードで高得点」を
除外できる。`sweep_id` は params の正規化JSONの SHA-256 先頭12桁で、同じ params なら
呼び出しをまたいでも同一になる決定的ハッシュ。

---

## 7. パラメータ探索ブリッジ

[optuna_bridge.py](../scripts/optuna_bridge.py) は親機で走り、クラスタを
ベイズ最適化の評価部として使う。

```
engine.ask() → params
  emit_trial_jobs: 1トライアル = instances(シード)数ぶんのジョブを pending へ投入
                   sweep_id = トライアルタグ(summarize --by-sweep と互換)
  結果を待つ(全 result.json が揃う / いずれか失敗 / trial-timeout 超過)
  collect_scores → aggregate(mean|min|max)
engine.tell(ref, params, value)   ※失敗トライアルは FAIL
```

- **in-flight 制限** `--parallel`(既定32): 同時進行トライアル数の上限。TPE は逐次性が
  あり高並列で質が落ちるため32程度に抑える
- **エンジン2種**:
  - Optuna(あれば): TPE + `JournalStorage`(`state/search/<name>.journal.log`、
    DBサーバ不要・再開可能)。optuna 3系/4系の import 差を吸収
  - 内蔵フォールバック(なければ): 標準ライブラリのみ。序盤ランダム探索、以降は
    現職近傍のガウス摂動による山登り。履歴は `state/search/<name>.history.jsonl`
  - `log` スケールの float/int も境界内にクリップして正しい型で返す(`clamp_numeric`)
- **再開安全**: ブリッジを止めて再実行しても、結果が既にある job_id は再投入せず再利用
- **可観測性**: `status/bridge-<name>.json` に進捗、`state/search/<name>.best.json` に最良

導入(オフライン wheel)は [offline_optuna.md](offline_optuna.md)。

---

## 8. データフロー全体図

```
make_job / optuna_bridge
        │  ジョブJSON
        ▼
  jobs/pending/ ──claim(os.replace)──▶ jobs/running/<job>--<worker>.json
                                              │ worker.py
                    repo_snapshot ──同期──▶ ローカル repo/(必要ならビルド)
                                              │ 実行(env注入・ハートビート)
                    artifacts ◀──回収──── ローカル repo/out/...
                                              │
              results/<worker>/<job>/{stdout,stderr,status,meta,result}.json
                                              │
                              jobs/done/ or jobs/failed/
                                              │
     summarize_results ◀──result.json──────────┘
        │ --update-incumbent
        ▼
  state/incumbent.json  ← 翌朝これと results/.../artifacts/ を富岳で検証
```

---

## 9. 既知の限界(意図的に受け入れているもの)

- **親機(共有フォルダ)が単一障害点**: 方式上不可避。個人利用・学内LANでは許容
- **共有フォルダ書込権限 = 全ワーカーでのコード実行権限**: `command` は各PCでそのまま
  実行される。ACL を出場メンバーに限定すること([supercon_cluster_policy.md](../supercon_cluster_policy.md))
- **ファイルポーリング由来の秒オーダー遅延**: 探索用途では無害
- **GPU 非活用**: 2026年の富岳はCPU(A64FX)。ローカルPCのGPU(RX 6300)も使わない
- **x86 と A64FX の性能差**: ローカルでの実行時間は富岳と相関しない。だから時間計測を
  せず、スコアと正しさだけを収穫する設計にしている

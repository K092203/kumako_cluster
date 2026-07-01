# SuperCon Local Worker Cluster

共有フォルダだけで動く、SuperCon検証用の疎結合ジョブクラスタです。

目的は、1つの巨大なMPIクラスタを作ることではなく、たくさんの独立した実験を20台程度のPCへ分配して高速に回すことです。

## 普段使うもの

親機で使うもの:

```powershell
.\make_job.bat ...
.\make_many_jobs.bat ...
.\make_stress_jobs.bat ...
.\status.bat
.\summarize.bat
.\requeue_failed.bat
.\start_all_slots.bat 14
```

子機で使うもの:

```powershell
.\setup_worker_auto.bat
.\start_launcher_agent.bat
.\start_worker_supervisor.bat 14
```

基本運用では `start_worker_supervisor.bat` を使います。1台につき14スロットをまとめて監視し、落ちたスロットは自動で起動し直します。

## フォルダ

```text
jobs/pending/   親機が投入した待機ジョブ
jobs/running/   Workerが取得した実行中ジョブ
jobs/done/      正常終了したジョブ
jobs/failed/    失敗・タイムアウトしたジョブ
results/        実行結果
status/         Workerの現在状態
control/        親機から子機へ置く命令
repo_snapshot/  Workerへ配る実行コード
state/          現在のベスト結果
scripts/        親機・Worker用スクリプト
workers/        worker番号の登録情報
```

## 子機の準備

各PCで一度だけ番号を割り当てます。

```powershell
.\setup_worker_auto.bat
```

親機から開始命令を受けたいPCでは、launcher agentを起動しておきます。

```powershell
.\start_launcher_agent.bat
```

直接そのPCでスロットを起動する場合:

```powershell
.\start_worker_supervisor.bat 14
```

親機から全PCへ開始命令を置く場合:

```powershell
.\start_all_slots.bat 14
```

## ジョブ投入

ダミーの軽いジョブ:

```powershell
.\make_job.bat --count 100
```

CPU負荷確認用ジョブ:

```powershell
.\make_stress_jobs.bat 5000 60
```

任意のコマンドを実行するジョブ:

```powershell
.\make_job.bat --count 280 --timeout-sec 60 -- python stress_solver.py --seconds 20 --workers 1 --seed __seed__
```

`__seed__` は各ジョブのseedへ自動で置き換わります。

## solverの置き場所

Workerに実行させたいファイルは `repo_snapshot/` に置きます。

solverが標準出力または標準エラーに次の形式を出すと、集計できます。

```text
#TUNE elapsed=12.34 score=98765 correct=1
```

## 状態確認と集計

```powershell
.\status.bat
.\summarize.bat
```

失敗ジョブをもう一度流す場合:

```powershell
.\requeue_failed.bat
```

## 設計上の注意

子機は共有フォルダを監視して、自分でジョブを取得します。親機は子機へ直接ログインしません。

実行ログ、ベンチ結果、一時ファイル、Worker生成物はGit管理や長期保存の対象ではありません。必要になったときにジョブを再投入して作り直します。

`runledger-main/` と `review-artifact-main/` は設計参考用です。通常の実行には直接使いません。

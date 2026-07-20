# 管理画面(admin panel)

親機のブラウザから、状態確認・ジョブ生成・パラメータ探索の起動・テンプレート管理・結果
アーカイブを1画面で行うためのローカルWebアプリ。既存のCLIツール(`make_job.py` /
`status.py` / `optuna_bridge.py` / `archive_results.py` 等)を置き換えるものではなく、
それらをブラウザから呼び出すための追加の窓口。**アクセスは `http://127.0.0.1:8765/`
(localhost)限定**で、他PCからは見えない・認証機構もない(その前提で設計している)。

管理画面が無くても、これまでどおり `.bat` 群と `python scripts\...` だけで全ての運用が可能。
管理画面は任意導入の追加機能で、Flask未導入でも他のスクリプトには一切影響しない。

---

## 導入(オフライン wheel バンドル)

Optuna と同じ考え方(`offline_optuna.md` 参照)。Flask とその依存
(Werkzeug / Jinja2 / MarkupSafe / itsdangerous / click / blinker、いずれも純粋な
Pythonパッケージ)を、ネットが使える環境で wheel として集めて持ち込む。

```bash
# ネット環境で実行。Windows向けに集める場合:
pip download flask -d wheels/ --only-binary=:all: \
    --platform win_amd64 --python-version 312
```

`wheels/` を `tools/wheels/` に配置し、親機で:

```bat
python -m pip install --no-index --find-links tools\wheels --user flask
```

Optuna Dashboard(§5「探索」タブから使う場合のみ必要)も同様に:

```bat
python -m pip install --no-index --find-links tools\wheels --user optuna-dashboard
```

導入できていなくても、管理画面自体の起動・状態確認・ジョブ生成・アーカイブ実行は動く
(Optuna Dashboardのボタンだけ「見つかりません」エラーになる)。

---

## 起動

```bat
admin_panel.bat
```

ブラウザで `http://127.0.0.1:8765/` を自動的に開く(サーバ起動より早く開こうとするので、
繋がらなければ手動でリロードする)。既定ポートは8765固定(変更したい場合は
`python scripts\admin_panel.py --port <番号>` を直接実行する)。

`Ctrl+C` で停止する。停止しても共有フォルダ上のジョブ・状態は消えない(このアプリは
`jobs/` `status/` `state/` を読み書きするだけで、他に副作用は持たない)。

---

## 画面の構成

### 状態
`status.py` と同じ内容を、3秒おきに自動更新するテーブルで表示する。ワーカー/supervisor/
launcher/探索ブリッジを種別ごとに分け、`idle`/`running`/`stale` を色付きバッジで示す。
ジョブキュー(pending/running/done/failed)の件数と、`state/incumbent.json` /
`state/search/*.best.json` の内容も表示する。

### ジョブ生成
2通りの投入方法がある。

- **範囲指定スイープ**: コマンドテンプレート・パラメータの範囲(low/high、`log`任意、
  またはcatのchoices)・件数・サンプリング方式(ランダム/グリッド)を指定し、「プレビュー」で
  実際に生成されるジョブとETA(所要時間見積もり)を確認してから「投入する」で
  `jobs/pending/` へ書き込む。ETAは稼働中スロット数と、過去の同一sweep_idの実測平均
  (無ければジョブのtimeout_sec)から概算する — 目安であり保証ではない。
  プレビューで見せた乱数値と実際に投入される値は一致する(内部でシードを固定して再現する)。
- **手作りJSONのZIP投入**: `jobs/pending/` に置きたい `.json` ファイルをまとめたZIPを
  ドラッグ&ドロップ(またはクリックして選択)する。job_id重複・不正JSON・command欠落などは
  自動的に検出してスキップし、理由を一覧表示する。プレビュー後、同じファイルを再送信して
  実際に投入する。

### 探索
`optuna_bridge.py` を画面から起動・停止できる。パラメータ範囲・コマンド・seed範囲・
max_trials・parallel・agg(mean/min/max)・direction(max/min)を指定して「起動する」を
押すと、spec を `state/panel_specs/<name>.json` に保存し、`optuna_bridge.py` を
バックグラウンドプロセスとして起動する。`--race`/`--hedge`/`--warm-start-from`/
`--tpe-profile` 等の詳細オプションは画面には出さず、コード側の推奨デフォルトのまま使う
(細かく調整したい場合はCLIから直接 `optuna_bridge.py` を使うこと)。

一覧には進捗(`status/bridge-<name>.json` の内容)とベストスコアが表示される。「停止」は
このパネルが起動したプロセスに対してのみ有効(**管理画面自体を再起動すると、それ以前に
起動した探索は「このパネルからは停止できません」状態になる** — その場合は
`control/stop_all` を使うか、該当プロセスを直接終了させること)。

Optunaエンジンを使った探索(`state/search/<name>.journal.log` がある場合)は、
一覧から「Dashboardを開く」で Optuna Dashboard を起動してブラウザで見られる
(内蔵フォールバックエンジンを使った探索には journal ログが無いため利用不可)。

### テンプレート
「ジョブ生成」「探索」タブの入力内容を、名前を付けて保存・再利用できる。各タブの
「テンプレートとして保存」ボタンで保存し、このタブの「読み込む」で対応するタブへ切り替えて
入力を復元する。保存内容は `state/panel_templates/` にJSONとして残る(削除も可能)。

### アーカイブ
`archive_results.py --root <root> [--label <label>]` を実行し、`jobs/done` `jobs/failed`
`results/` を `archive/<label または日付>/` へ退避する。実行結果(done/failed件数)を表示する。

---

## 既知の限界

- **localhost限定**: 他PCのブラウザからはアクセスできない設計。複数メンバーで共有したい場合は
  各自のPCで別々に(それぞれの `--root` を同じ共有フォルダに向けて)起動すること。
- **認証なし**: このPCを使える人なら誰でも操作できる。ジョブ投入・探索起動は共有フォルダへの
  書き込みそのものであり、実質的な権限は他のCLIツールと同じ(`supercon_cluster_policy.md`
  参照)。
- **探索の停止は管理画面のプロセス内メモリに依存**: 上記「探索」タブの説明のとおり、
  管理画面自体の再起動で個別停止の追跡は失われる(探索プロセス自体は動き続ける)。
- **claim_job/copy_repo_snapshot と同様、管理画面のFlask開発サーバ自体もSMBハング対策の
  対象外**: 管理画面はあくまで便利ツールであり、本体クラスタ(worker.py等)の可用性には
  影響しない。管理画面が固まっても、`.bat` からの直接操作は常に可能。

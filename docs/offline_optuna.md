# Optuna のオフライン導入(親機用)

`scripts/optuna_bridge.py` は Optuna があれば TPE で、なければ内蔵の
ランダム+山登りサンプラーで動く。Optuna を使うほうが少ない試行数で良い
パラメータに到達しやすいので、可能なら親機に導入する。

探索の仕組み全体は [architecture.md](architecture.md) の「7. パラメータ探索ブリッジ」節、
スペックの書き方は [job-format.md](job-format.md) の「探索スペック」節を参照。

## なぜ `git clone optuna/optuna` ではダメか

Optuna 本体は numpy / colorlog / alembic / sqlalchemy / tqdm / PyYAML 等に
依存しており、ソースを clone しただけでは import できない。依存ごと
持ち込むには **wheel バンドル方式**を使う。

## 手順(ネットが使える環境で)

親機と同じ Python のメジャーバージョン(例: 3.12)・同じ OS/アーキテクチャ
(Windows x64)向けに wheel を集める:

```bash
# ネット環境(自宅PC等)で実行。Windows向けに集める場合:
pip download optuna -d wheels/ --only-binary=:all: \
    --platform win_amd64 --python-version 312
# 同じ環境同士(親機もLinuxなど)なら --platform 等は不要:
pip download optuna -d wheels/
```

`wheels/` ディレクトリを USB か共有フォルダで持ち込み、`tools/wheels/` に配置。

## 手順(親機で・管理者権限不要)

```bat
python -m pip install --no-index --find-links tools\wheels --user optuna
python -c "import optuna; print(optuna.__version__)"
```

`--user` はユーザーディレクトリにインストールするため管理者権限不要。

## pip 自体が使えない場合

導入を諦めてフォールバックで運用する:

```bat
python scripts\optuna_bridge.py --spec my_search.json --engine builtin
```

内蔵エンジンはランダム探索+現職近傍の摂動(山登り)で、標準ライブラリ
のみで動く。TPE より収束は遅いが、夜間の大量試行なら実用になる。

## ストレージについて

- Optuna: `state/search/<name>.journal.log`(JournalStorage、DBサーバ不要、再開可能)
- 内蔵エンジン: `state/search/<name>.history.jsonl`(再開可能)

ブリッジを Ctrl+C で止めても、同じ spec で再実行すれば続きから探索する。
投入済みジョブの結果は再利用される(同じ job_id の結果があれば再実行しない)。

## バージョンについて

`optuna_bridge.py` は JournalStorage のバックエンド import を optuna 3系
(`JournalFileStorage`)と 4系(`storages.journal.JournalFileBackend`)の両方に
対応させてある。どちらの系列でも動くので、wheel を集める際にバージョンを
固定する必要はない。動作確認は次で足りる:

```bat
python -c "import optuna; from optuna.storages import JournalStorage; print(optuna.__version__)"
```

内蔵エンジンと Optuna は探索履歴の保存先が別(`.history.jsonl` と `.journal.log`)
なので、途中でエンジンを切り替えると履歴は引き継がれない。本選中はどちらか一方に
統一すること。

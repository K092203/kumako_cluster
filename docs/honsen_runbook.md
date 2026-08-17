# SuperCon2026 本選 runbook

本選: 2026年8月17日(月)〜21日(金)、富岳、オンライン(学校から参加)。
最終提出: **20日(木)13:00 厳守**。

関連: クラスタの内部構造は [architecture.md](architecture.md)、JSONの仕様は
[job-format.md](job-format.md)、規則対応チェックリストは
[supercon_cluster_policy.md](../supercon_cluster_policy.md)「本選モード」節。

## 富岳利用時間帯(規則1条・時間帯外アクセス禁止)

| 日 | 富岳 | ローカルクラスタ |
|---|---|---|
| 17(月) | 13:00〜17:00 | 終日(問題公開13:00以降) |
| 18(火) | 9:00〜17:00 | 終日 |
| 19(水) | 9:00〜17:00 | 終日 |
| 20(木) | 9:00〜13:00 | 〜13:00(提出まで) |

**夜間(17:00〜翌9:00)はローカルクラスタが唯一の計算資源。** ここで探索を回し切る。

## SuperCon2026 本選ソルバの実験 (2026-08-17 夜〜)

本選ソルバの **seed 分散** と **dL 感度** を測る一式は
[../experiments/sc26/README.md](../experiments/sc26/README.md) にある。

- 何を決めるための実験か: 「同じ総資源を 1 本の長時間探索に使うか、
  seed を変えた複数 replica に分散するか」
- 時間による改善量は富岳で測定済み(A64FX でしか測れない)。
  **seed による分散を Kumako で測る**
- ⚠️ `repo_snapshot/` は `.gitignore` 対象なので、`experiments/sc26/` から
  コピーして使う。手順は上記 README
- ⚠️ **いきなり数百 job を投げない。** 先に smoke test(README §4)を通す
- ⚠️ Kumako の絶対速度を富岳へ外挿しない。使えるのは分布形状と相対比較のみ

## 日次サイクル

```
 9:00 富岳開始。前夜の state/search/<name>.best.json と
      results/<worker>/<job>/artifacts/ の最良解を富岳で検証
      (コンパイル・スケール確認・提出候補の実測)
昼〜  アルゴリズム改良。改良版を repo_snapshot/ へ配置(自動再ビルド)
16:30 富岳終了前に翌朝検証したいものを整理
17:00 探索スペックを見直して optuna_bridge を夜間分再起動
      status.bat で全ワーカー稼働を確認してから帰る
夜間  無人探索(supervisor が落ちたスロットを自動再起動)
翌朝  requeue_failed.bat --stale-running-sec 600 で孤児回収 → 9:00へ
```

### 朝の富岳検証(具体手順)

前夜の探索が出した最良を、富岳で「本当に良いか・正しいか」確認して提出候補にする。

```bat
rem 1. 夜間探索の最良パラメータと最良解を確認
type state\search\<name>.best.json
python scripts\summarize_results.py --by-sweep --agg min

rem 2. 最良 sweep のパラメータで、提出用インスタンスの解を富岳で再生成
rem    (ローカルの解は x86 産なので、富岳で同パラメータを実行し直す)

rem 3. 生成した解を独立採点で検証してから提出
rem    (模擬問題なら examples\mock_problem\scorer.py が採点器の代役)
```

ローカルの `results\...\artifacts\` の解は「良いパラメータの当たり」を知るために使い、
提出物そのものは富岳で再生成・検証する。x86 と A64FX で結果が変わりうるため。

## 8/17(月) 問題公開からクラスタ稼働まで(目標2時間)

1. [ ] 問題読解・入出力形式の確認(チーム全員)
2. [ ] 配布物(生成器・サンプル)を `repo_snapshot/` に配置
3. [ ] ソルバー第1版(貪欲でよい)を作り、`#TUNE score=... correct=...` を stderr に出す規約を実装
4. [ ] ローカル検証: `python solver.py --seed 1` が動くこと
5. [ ] C++なら `cluster_setup.json` を書き `verify_toolchain.bat` 相当で1ジョブ確認
6. [ ] `make_job.bat --count 20 --param ... -- <command>` で小規模投入 → `status.bat` / `summarize.bat`
7. [ ] 探索スペック(`templates/search_spec.example.json` をコピー)を書き `optuna_bridge` 起動
8. [ ] 全20台で `start_worker_supervisor.bat <slots>` 起動(slotsはリハーサルの実測値)

## コマンド早見表(親機)

| 目的 | コマンド |
|---|---|
| 状態確認 | `status.bat`(stale が出たら該当PCを見る) |
| ジョブ投入 | `make_job.bat --count N --param k=v --artifact out/sol.txt -- <cmd>` |
| 探索開始 | `python scripts\optuna_bridge.py --spec spec.json --max-trials 1000 --agg min` |
| 集計 | `summarize.bat --by-sweep --agg min`(表に95%CI列。CIが重なる2案は差を判定不能) |
| スロット自動増減 | `python scripts\supervise_slots.py --slots 14 --adapt`(負荷変動時の保険) |
| 失敗再投入 | `requeue_failed.bat` |
| 孤児回収 | `requeue_failed.bat --stale-running-sec 600` |
| 全停止 | `control\stop_all` ファイルを作る(`type nul > control\stop_all`) |
| 再開 | `control\stop_all` を削除 → 各PCで supervisor 再起動 or `start_all_slots.bat` |
| 結果退避 | `python scripts\archive_results.py`(探索フェーズの区切りごと) |

## 障害復旧

| 症状 | 対処 |
|---|---|
| status で特定ワーカーが stale | PC再起動→ `start_worker_supervisor.bat`。ジョブは孤児回収で戻る |
| pending が減らない | 全ワーカー stale なら共有フォルダ/親機のネットワークを確認 |
| setup失敗が連発 | ワーカーPCの `<local-dir>\setup.log` を確認。cluster_setup.json のパス間違いが典型 |
| ブリッジが進まない | `status.bat` で bridge-* の in_flight を確認。trial-timeout で自動的に FAIL 処理される |
| 誤ったジョブを大量投入 | `control\stop_all` → `jobs\pending\*.json` を削除 → stop_all 削除 |

## 規則遵守の注意(policy md「本選モード」も参照)

- 本選中は **git push しない**(ソルバー・課題情報は共有フォルダのみ)
- SNS・ブログへの投稿禁止(20日14:00まで)
- 解法の相談はメンバー内のみ。Discordの大会連絡は毎朝確認
- 富岳の時間帯外アクセス禁止(ローカルクラスタは制限なし)

## 事前準備チェックリスト(〜8/16)

- [ ] policy md「本選モード」チェックリスト完了(private化・filter-repo・ACL)
      ※private化は完了(2026-07-04)。履歴除去は [history-purge.md](history-purge.md) 参照
- [ ] 全PC(親機・子機)で `python --version` が **3.9 以上**であることを確認
      (3.9未満では全スクリプトが起動時に明示エラーで止まる)
- [ ] w64devkit 配置 + 学校PC1台で `verify_toolchain.bat` 実証
- [ ] optuna wheel バンドル導入(`docs/offline_optuna.md`)
- [ ] スロット数の実測決定(下記ベンチ手順)
- [ ] 模擬本選リハーサル(`examples/mock_problem/` で月13時→木13時の通し)
- [ ] PC室の夜間電源ポリシー確認(自動シャットダウン・スリープ設定の有無)

## スロット数ベンチ手順(§3.2: 14 vs 20)

1. 1台で `start_worker_supervisor.bat 14` を起動
2. 親機: `python scripts\bench_throughput.py --minutes 10 --count 2000 --seconds 30`
3. 終了後、そのPCの supervisor を止め(`control\<worker>.slots.stop`)、`start_worker_supervisor.bat 20` で再起動
4. 同じベンチをもう一度実行し、jobs/min の大きい方を採用
5. slot=20 でタスクマネージャのメモリが 15GB に迫るなら 14 に落とす
6. 本番ソルバー完成後に同じ手順で再実測して確定

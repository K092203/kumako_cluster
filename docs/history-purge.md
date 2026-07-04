# git履歴からの機密除去手順(workers/*.json)

過去コミットに `workers/*.json`(実機ホスト名 `Z2-0PC31` 等・学籍ID `s241612`)が
残っている。トラッキング解除(.gitignore)では**過去の履歴からは消えない**ため、
`git filter-repo` で履歴自体を書き換えて force push する。

- リポジトリは private 化済み(2026-07-04)。それでも履歴除去は実施すること
  (将来 public に戻す場合や、clone の拡散に備えるため)
- **force push は破壊的操作**。以下を読んでから、自分の手で実行すること

## 前提確認(実行前)

```bash
# 全PRがマージ済みで、作業ツリーがクリーンなこと
git checkout main && git pull
git status --short        # 何も出ないこと

# 機密が履歴に残っていることの確認(2コミット出るはず)
git log --all --oneline -- workers/
```

## 手順

```bash
# 1. git-filter-repo の導入(pip でよい)
pip install git-filter-repo

# 2. 念のためバックアップ(フォルダごとコピー)
cd ..
cp -r kumako_cluster kumako_cluster.bak
cd kumako_cluster

# 3. 履歴から workers/ を全削除(全ブランチ・全タグが対象)
#    filter-repo は安全のため remote 設定を消すので、後で追加し直す
git filter-repo --path workers --invert-paths --force

# 4. remote を再設定して全ブランチを force push
git remote add origin https://github.com/K092203/kumako_cluster.git
git push --force --all origin
git push --force --tags origin
```

## 検証(実行後)

```bash
# 何も出なければ成功
git log --all --oneline -- workers/

# blob レベルでも消えていることを確認(エラーになれば成功)
git show 461cb02:workers/worker01.json   # → fatal になること
                                          # (コミットIDは書き換えで変わるので、
                                          #  そもそも 461cb02 が存在しなくなる)

# GitHub 側でも確認: リポジトリの検索で s241612 がヒットしないこと
```

## 実行後の注意

1. **全コミットIDが変わる**。既存の clone(他のPC・チームメイト)は
   `git pull` できなくなるので、**全員 clone し直す**こと:
   ```bash
   rm -rf kumako_cluster && git clone https://github.com/K092203/kumako_cluster.git
   ```
2. マージ済みPR(#1〜#9)の表示はGitHub上に残るが、リンク先コミットは
   孤立オブジェクトになる。GitHubのキャッシュから完全に消したい場合は
   GitHub Support に "remove cached views / dangling commits" を依頼できる
   (private化済みなら第三者アクセスは既に遮断されている)
3. 共有フォルダ上の実運用ディレクトリ `workers/` はこの操作と無関係
   (git 管理外で、実行時の登録台帳として引き続き使う)
4. 完了したら [supercon_cluster_policy.md](../supercon_cluster_policy.md)
   「本選モード」チェックリストの filter-repo 項目にチェックを入れる

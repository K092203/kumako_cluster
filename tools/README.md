# tools/ — ポータブルツールチェーン置き場

管理者権限なしの学校PCで C/C++ ソルバーをビルドするためのツールを置くディレクトリ。
ツール本体はサイズが大きいため git 管理しない(このREADMEのみコミット)。

## w64devkit の配置(管理者権限不要)

1. ネットが使える環境で w64devkit の zip を取得する
   - https://github.com/skeeto/w64devkit/releases から `w64devkit-x64-*.exe`(自己展開) or zip
2. 展開して `tools/w64devkit/` に置く(`tools/w64devkit/bin/g++.exe` が存在する状態にする)
3. 共有フォルダ経由で全ワーカーから参照可能になる

## ワーカーからの参照方法

worker.py はジョブ・setupコマンドの環境変数に `SUPERCON_ROOT`(共有クラスタルート)を
注入する。**文字列コマンド(shell経由)なら** `%SUPERCON_ROOT%` が展開される:

```json
{
  "setup_command": "\"%SUPERCON_ROOT%\\tools\\w64devkit\\bin\\g++.exe\" -O2 -march=native -o solver.exe solver.cpp",
  "timeout_sec": 300
}
```

この JSON を `repo_snapshot/cluster_setup.json` として置くと、各ワーカースロットが
スナップショットを取り込んだ直後に**1回だけ**ローカルディスク上でビルドを実行する
(スナップショット内容が変わると自動で再ビルド)。ビルドログは各PCの
`<local-dir>/setup.log` に残る。

注意: リスト形式コマンド(`["...g++.exe", ...]`)は shell を経由しないため
`%SUPERCON_ROOT%` が展開されない。setup では文字列コマンドを使うこと。

## 動作確認

親機で `verify_toolchain.bat` を実行 → ワーカーを1つ起動 →
`results\<worker>\hello001\stdout.txt` に `hello from kumako cluster` が出ればOK。

## その他の注意

- SMB上で .exe を直接実行しない(worker はローカルにコピーしてから実行するので通常は問題ない)
- ウイルス対策ソフトが g++/生成exeをブロックする場合は学校の管理者に相談
- ローカル検証には富岳と同じ gcc 系(w64devkit は GCC)を使うことで、コンパイラ差による
  未定義動作の顕在化を早期に検知できる。ただし x86 と A64FX の性能差は探索では無視する(設計書§1.3)

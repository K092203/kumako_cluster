> 🌐 [English](README.md) · [日本語](README.ja.md) · [中文](README.zh.md)

# review-artifact

**実験成果物(ログ・ジョブ出力)・差分・任意ファイルを read-only の AI で triage する CLI。**
ジョブのログ・差分・ファイルを渡すと、レビュー結果を再現可能な **Markdown / JSON** として保存します。
**reviewer はあなたのリポジトリを書き換えません。**

```text
実験ログ・ジョブ成果・差分を、読み取り専用の AI に解析させ、結果を
Markdown / JSON の成果物として残す。reviewer はリポジトリを書き換えない。
「回したが何が起きたか分からないログ」の triage が主用途。
```

- **ステータス:** v0.1.2 · MIT · 標準ライブラリのみ、実行時の外部依存ゼロ
- **検証:** テスト24件(Python 3.12)+ 実 `codex`/gpt-5.5 の end-to-end 実行(2026-07-01)— [検証結果](#検証結果)参照
- **既知の制約:** reject/relocate のガードはテストで実証済みだが、実モデルの hallucination を捕まえた実例はまだ無い([既知の制約](#既知の制約)参照)

---

## なぜ作るか

ジョブを回したものの、`meta.json` / `stderr` / `resource` を見ても、失敗したのか・timeout
したのか・ボトルネックに当たったのかが即座には分からない。`review-artifact` はそれを
**read-only** の AI で triage し、結果を成果物として保存します — エージェントにリポジトリを
書き換えさせる代わりに。主用途は**実験ログの triage**で、差分・ファイルレビューは副次的です。

## 既存ツールとの違い

| 比較対象 | review-artifact が足すもの |
|---|---|
| **fabric / llm / mods** | target collector(git diff / ログ束 / ファイル)+ **outcome 付き JSON findings** + **成果物ファイル保存**。これらは「テキスト → AI → テキスト」止まり。 |
| **CodeRabbit / Copilot / PR bot** | **ローカル・read-only・成果物優先**で、サーバ側 PR 差分ではなく**実験ログ**が中心。 |

**主対象(wedge):** `meta.json`/`stderr`/`resource` を見ても失敗・ボトルネックが即断できない
ジョブ群を、read-only で構造化 triage して成果物に残したい人。差分レビューは対応するが“ついで”。

## 成果物を信頼できるものにする3点

1. **契約として read-only** — CLI は収集対象を読むだけ。prompt でも「編集できない」と明示。
   エージェントがリポジトリを変更しない。
2. **引用根拠を検証する** — LLM は file/line を捏造する。reviewer に**引用テキストを
   一字一句そのまま**出させ、それを実際に収集した内容に対して照合する。**捏造された引用は
   却下**(`line_verified: false`)、**正しい引用が別の行を指していれば自動補正**
   (`line_relocated: true`)。引用が無い場合は行の存在チェックにフォールバック。
   findings は常に advisory。
3. **秘密情報は既定でスキップ** — `.env` / `*.pem` / `*token*` 等は `--allow-sensitive`
   を付けない限り収集しない。

3点とも[検証結果](#検証結果)で実証しています。

## インストール

```bash
cd review-artifact
pip install -e ".[dev]"
```

Python 3.11 以上が必要。実行時の外部依存はありません(pytest は開発用のみ)。

## クイックスタート(logs が主役)

```bash
# 主用途: fake バックエンドでジョブ成果を triage（API キー不要）
review-artifact logs examples/sample-results --backend fake

# AI を呼ばずに「何を送るか」だけ確認
review-artifact logs examples/sample-results --dry-run

# simonw/llm を使う場合（llm は別途インストール）
review-artifact logs results/latest --backend llm --language ja

# 副次: 差分 / ファイルレビュー
review-artifact diff --dry-run
review-artifact files README.md --backend fake
```

成果物は既定で `.review/` に出力されます(`<ts>-<target>.md` / `.json`)。

## コマンド

```bash
review-artifact logs <dir>          # ★ 主用途: ジョブ/実験ログの triage
review-artifact diff                # git diff レビュー（副次）
review-artifact files <paths...>    # ファイルレビュー
review-artifact ask "質問" --files src/foo.cpp
```

## 出力成果物（JSON, schema v1）

```json
{
  "schema_version": 1,
  "summary": "...",
  "findings": [
    {
      "severity": "medium",
      "title": "...",
      "body": "...",
      "file": "examples/sample-results/stderr.txt",
      "line": 2,
      "evidence": "deadline reached",
      "line_verified": true,
      "line_relocated": false,
      "confidence": "medium"
    }
  ],
  "open_questions": [],
  "raw_output": "..."
}
```

Markdown は人間向け、JSON は CI / bot / 別 AI 向け。findings は **advisory** で、
file/line は可能な限り収集内容に対して検証し、未検証の行は null 化します。

## バックエンド（v0.1 から multi-backend）

| バックエンド | 説明 |
|---|---|
| `llm` | [simonw/llm](https://github.com/simonw/llm) — **既定推奨** |
| `codex` | `codex exec --sandbox read-only` |
| `custom` | 任意の read-only コマンド（prompt を stdin で渡す） |
| `fake` | テスト/CI 用の固定出力（ネットワーク不要） |

詳細は [docs/backends.md](docs/backends.md)。

## 検証結果

**Python 3.12.3 / Linux 6.18 (WSL2) / 2026-07-01** で取得。

### 行検証 — 存在チェック vs 根拠チェック

看板機能を、実際の `examples/sample-results/stderr.txt`
(`2行目 = "ERROR: deadline reached before completion"`)で実証。旧ロジックは
「その行は存在するか?」だけ、新ロジックは「引用された根拠が実際にそこにあるか?」を問う:

```text
ケース                   | BEFORE (存在?)           | AFTER (根拠チェック)
-------------------------|--------------------------|------------------------------
正しい引用               | line=2 verified=True     | line=2 verified=True
正しい引用・行番号違い    | line=5 verified=True     | line=2 verified=True relocated
捏造された根拠            | line=4 verified=True     | line=None verified=False
引用なし(従来)         | line=4 verified=True     | line=4 verified=True
```

中央の2行が勝ち筋: 正しい引用が誤った行を指していれば**自動補正**され、旧チェックが
平然と "verified" にしていた捏造引用は**却下**される。(この2行は crafted な finding を
使用。実モデルでの注意点は下の real-LLM 実行を参照。)

### 実 LLM での end-to-end(codex / gpt-5.5)

2026-07-01 に `codex` バックエンド(`codex exec --sandbox read-only`)で実際に実行:

```bash
review-artifact logs examples/sample-results --backend codex
```

2回の実走(サンプルログ + 60行の反復ログ)で gpt-5.5 は **8 findings / 7 引用を生成し、
すべての引用が verbatim かつ正しい行**だった(ソースと手作業で照合)。ガードによる
**誤却下はゼロ**。例えば 60 行のログでは、ほぼ同一の行が並ぶ中で発散を
`stdout.txt:41`(`iter 41: residual=nan`)に正しく特定した。

**正直な注意点:** これらの実走で gpt-5.5 は引用を捏造しなかったため、*reject/relocate*
の経路は決定論テストと上の before/after 表でのみ実証されており、**実モデルの誤りでは
発火していない**。この実走が証明するのは「実 LLM で loop が成立すること」と
「ガードが正しい出力を壊さないこと」であり、**実 hallucination を捕まえた証明ではない**。

### テストスイート — 24件 green

```text
tests/test_artifacts.py  2   （成果物の命名, markdown + json writer）
tests/test_cli.py        3   （logs/diff/files を fake backend で）
tests/test_collect.py    7   （git/file/dir collector, サイズ上限, binary + 秘密 skip）
tests/test_config.py     4   （既定値, TOML 上書き, merge）
tests/test_findings.py   8   （JSON 抽出; 行検証: 存在 / 根拠一致 / 自動補正 / 捏造却下）
                        ---
                         24   passed in 0.24s
```

### 振る舞いの確認（エンドツーエンド・fake backend）

| 振る舞い | コマンド | 観測結果 |
|---|---|---|
| ログ triage（主用途） | `logs examples/sample-results --backend fake` | `.review/…-logs….md` + `.json` を生成 |
| **根拠が検証される** | fake が `stderr.txt:2` を根拠 `"deadline reached"` で引用 | 2行目に一致 → `line_verified=true` |
| **捏造が却下される** | fake がログに無い `"Segmentation fault"` を引用 | `line=null line_verified=false`、*citation rejected* と表示 |
| dry-run（AI 非呼出） | `logs … --dry-run` | 収集 bundle をそのまま表示、何も呼ばない |
| **秘密ガード** | `files .env --dry-run` | `skipped .env: sensitive`（含めるには `--allow-sensitive`） |
| バックエンド選択 | `--backend bogus` | `invalid choice …（llm, codex, custom, fake）`、`exit=2` |

代表的な出力(fake バックエンドが生成した Markdown 成果物):

```markdown
## Findings
- **medium**: Job timed out (examples/sample-results/stderr.txt:2, verified)
  stderr reports the deadline was reached before completion.
  evidence: `deadline reached`
- **low**: Claimed segfault (fabricated evidence) (examples/sample-results/stderr.txt, citation rejected: evidence not found)
  Reviewer claims a segfault, but no such text exists in the logs.
```

```console
$ review-artifact files .env --dry-run
--- COLLECTION NOTES ---
skipped .env: sensitive
```

CI は同じスイートを Python 3.12 + fake バックエンドで実行します(API 呼び出しなし)。

## セキュリティ

- 収集したファイル内容を、設定したバックエンドに送信します。
- 秘密らしきファイル(`.env`, `*.pem`, `*token*` 等)は既定でスキップ。
- `--allow-sensitive` は意図的なときだけ使用。
- 秘密ガードは補助であり完全な保証ではありません — 共有前に確認を。
- 詳細は [docs/SECURITY.md](docs/SECURITY.md)。

## ドキュメント

- [docs/configuration.md](docs/configuration.md) — `.review-artifact.toml`
- [docs/backends.md](docs/backends.md) — llm / codex / custom / fake
- [docs/prompts.md](docs/prompts.md) — prompt preset
- [docs/SECURITY.md](docs/SECURITY.md) — 秘密情報の扱い

例: [examples/sample-results](examples/sample-results)(fake バックエンドのデモ)、
[examples/fugaku-results.toml](examples/fugaku-results.toml)(HPC 設定例)。

## runledger との組み合わせ

[runledger](https://github.com/K092203/runledger) は review-artifact が triage する
run snapshot を生成します:

```bash
runledger run -- ./solver < input.txt
review-artifact logs runs/latest      # snapshot を read-only の AI で triage
```

## 既知の制約

- **`codex`/gpt-5.5 は end-to-end 検証済み(2026-07-01)。`llm` は未実行。** CI と
  自動テストは `fake` バックエンドを使用(ネットワーク/API なし)。実 `codex` 実走では
  モデルが引用を捏造しなかったため、reject/relocate の経路は決定論テスト + before/after
  表で実証されており、**実モデルの hallucination を捕まえた実例ではまだない**。
- **検証されるのは引用であって結論ではない。** finding は実在する行を引用しつつ誤った
  推論をしうる。`line_verified` は「引用が本物」を意味し、「finding が正しい」ではない。
  findings は advisory。
- **read-only の強度はバックエンドの sandbox に依存。** CLI 自体は読むだけですが、
  バックエンドが独自に動作しないことまでは保証できません。
- **行検証と JSON findings は best-effort。** reviewer の JSON を parse できない場合は
  `raw_output` を保持し `findings: []` とします。

## 非目標（v0.1）

自動修正 / 自動 commit、GitHub PR コメント bot、web UI / TUI、provider の完全抽象化、
セキュリティ監査ツール化。

## ライセンス

MIT — [LICENSE](LICENSE) を参照。

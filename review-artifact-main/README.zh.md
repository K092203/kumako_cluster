> 🌐 [English](README.md) · [日本語](README.ja.md) · [中文](README.zh.md)

# review-artifact

**对实验产物（日志、作业输出）以及 diff 和文件进行只读 AI 分诊。**
把它指向某次作业的日志、一个 diff 或任意文件；它会把评审结果保存为可复现的
**Markdown / JSON**。**评审器永远不会修改你的仓库。**

```text
Triage your experiment artifacts with a read-only AI.
Point it at a job's logs (meta.json, stderr, resource), a diff, or any files,
and save the review as reproducible Markdown/JSON — the reviewer never edits your repo.
```

- **状态：** v0.1.2 · MIT · 纯标准库，无运行时依赖
- **已验证：** 24 项测试（Python 3.12）+ 一次真实的 `codex`/gpt-5.5 端到端运行，2026-07-01 —— 见[验证](#验证)
- **已知限制：** reject/relocate 防护已由测试证明；尚未观测到捕获一次真实模型的幻觉（见[已知限制](#已知限制)）

---

## 为什么

你跑完了一个作业；但 `meta.json` / `stderr` / `resource` 并不能立刻告诉你它是失败了、
超时了，还是撞上了瓶颈。`review-artifact` 用**只读** AI 对其分诊，并把结果保存为产物 ——
而不是让一个 agent 去改动你的仓库。主要用途是**实验日志分诊**；diff 和文件评审是次要的。

## 与同类工具的区别

| 对比 | review-artifact 额外提供 |
|----|---------------------------|
| **fabric / llm / mods** | 目标收集器（git diff、日志收集包、文件）+ **带 outcome 的 JSON findings** + 保存的**产物文件**。这些工具止步于「文本 → AI → 文本」。 |
| **CodeRabbit / Copilot / PR bot** | **本地、只读、以产物为先**，并以**实验日志**为中心 —— 而非服务器端的 PR diff。 |

**切入点（wedge）：** 你跑过的作业，其 `meta.json`/`stderr`/`resource` 并不能让失败或瓶颈一目了然，
而你想要一份只读、结构化、并保存为产物的分诊。diff 评审受支持但属次要。

## 是什么让产物可信

1. **以契约保证只读** —— CLI 只读取它收集到的内容；prompt 也告知评审器它无法编辑。没有 agent 会改动你的仓库。
2. **引用经过证据校验** —— LLM 会捏造 file/line。评审器必须**逐字引用**它所引用的文本，
   而该引用会与实际收集到的内容进行校验。**捏造的引用会被拒绝**（`line_verified: false`），
   **指向错误行的真实引用会被自动校正**（`line_relocated: true`）。当未给出引用时，回退为纯行存在性检查。
   findings 始终是 advisory（仅供参考）。
3. **默认跳过密钥** —— `.env`、`*.pem`、`*token*` 等默认不会被收集，除非你传入 `--allow-sensitive`。

三者都在[验证](#验证)中得到演示。

## 安装

```bash
cd review-artifact
pip install -e ".[dev]"
```

需要 Python 3.11+。无第三方运行时依赖（pytest 仅用于开发）。

## 快速开始（日志优先）

```bash
# 主要用途：用 fake 后端分诊作业产物（无需 API key）
review-artifact logs examples/sample-results --backend fake

# 在不调用任何 AI 的情况下，查看究竟会发送什么
review-artifact logs examples/sample-results --dry-run

# 使用 simonw/llm（需另行安装 llm）
review-artifact logs results/latest --backend llm --language ja

# 次要：diff / 文件评审
review-artifact diff --dry-run
review-artifact files README.md --backend fake
```

产物默认写入 `.review/`（`<ts>-<target>.md` / `.json`）。

## 命令

```bash
review-artifact logs <dir>          # ★ 主要：作业/实验日志分诊
review-artifact diff                # git diff 评审（次要）
review-artifact files <paths...>    # 文件评审
review-artifact ask "question" --files src/foo.cpp
```

## 产物（JSON，schema v1）

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

Markdown 面向人类；JSON 面向 CI / bot / 另一个 AI。findings 是 **advisory** ——
file/line 引用会尽可能与收集内容校验，未通过校验的行会被置空。

## 后端（v0.1 起即多后端）

| 后端 | 说明 |
|---------|-------------|
| `llm` | [simonw/llm](https://github.com/simonw/llm) —— **推荐默认** |
| `codex` | `codex exec --sandbox read-only` |
| `custom` | 任意只读命令（prompt 通过 stdin 传入） |
| `fake` | 用于测试 / CI 的固定输出（无网络） |

见 [docs/backends.md](docs/backends.md)。

## 验证

采集环境：**Python 3.12.3 / Linux 6.18 (WSL2) / 2026-07-01**。

### 行校验 —— 仅存在性 vs 基于证据

招牌功能，针对真实的 `examples/sample-results/stderr.txt`
（`第 2 行 = "ERROR: deadline reached before completion"`）证明。旧检查只问
「这一行存在吗？」；新检查问「引用的证据真的在那里吗？」：

```text
case                     | BEFORE (exists?)         | AFTER (evidence-checked)
-------------------------|--------------------------|------------------------------
correct citation         | line=2 verified=True     | line=2 verified=True
right quote, wrong line  | line=5 verified=True     | line=2 verified=True relocated
FABRICATED evidence      | line=4 verified=True     | line=None verified=False
no quote (legacy)        | line=4 verified=True     | line=4 verified=True
```

中间两行是关键胜场：指向错误行的真实引用被**自动校正**，而旧检查会欣然「verified」的捏造引用
现在被**拒绝**。（这两行使用的是构造出来的 findings；真实模型上的注意点见下方 real-LLM 运行。）

### 真实 LLM 端到端（codex / gpt-5.5）

2026-07-01 用 `codex` 后端（`codex exec --sandbox read-only`）真实运行：

```bash
review-artifact logs examples/sample-results --backend codex
```

在两次真实运行（示例日志 + 一份 60 行的迭代日志）中，gpt-5.5 产生了
**8 条 findings / 7 处引用，且每一处引用都是逐字且落在正确行上** —— 由人工与源文件逐一核对。
该防护造成的**误拒为零**；例如在 60 行日志中，它在几乎相同的众多行里准确把发散定位到
`stdout.txt:41`（`iter 41: residual=nan`）。

**诚实的注意点：** 在这些运行中 gpt-5.5 **没有**捏造引用，因此 *reject/relocate* 路径仅由
确定性测试与上方 before/after 表格触发 —— **并非**由真实模型的错误触发。这次真实运行证明了
该闭环在真实 LLM 上成立、且防护不会破坏正确的输出；但它**尚未**展示捕获一次真实幻觉。

### 测试套件 —— 24 项通过

```text
tests/test_artifacts.py  2   (产物命名, markdown + json writer)
tests/test_cli.py        3   (logs/diff/files via fake backend)
tests/test_collect.py    7   (git/file/dir 收集器, 大小上限, binary + 密钥跳过)
tests/test_config.py     4   (默认值, TOML 覆盖, merge)
tests/test_findings.py   8   (JSON 提取; 行校验: 存在 / 证据一致 / 重定位 / 捏造拒绝)
                        ---
                         24   passed in 0.24s
```

### 行为验证（端到端，fake 后端）

| 行为 | 命令 | 观测结果 |
|----------|---------|----------|
| 日志分诊（主要） | `logs examples/sample-results --backend fake` | 写入 `.review/…-logs….md` + `.json` |
| **证据通过校验** | fake 引用 `stderr.txt:2`，证据 `"deadline reached"` | 引用在第 2 行找到 → `line_verified=true` |
| **捏造被拒绝** | fake 引用日志中不存在的 `"Segmentation fault"` | `line=null line_verified=false`，标记为 *citation rejected* |
| dry-run（不调用 AI） | `logs … --dry-run` | 打印精确的收集包，不调用任何东西 |
| **密钥防护** | `files .env --dry-run` | `skipped .env: sensitive`（需 `--allow-sensitive` 才包含） |
| 后端选择 | `--backend bogus` | `invalid choice … (llm, codex, custom, fake)`，`exit=2` |

代表性输出（fake 后端生成的 Markdown 产物）：

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

CI 在 Python 3.12 上用 fake 后端运行同一套测试（无 API 调用）。

## 安全

- 会把收集到的文件内容发送给你配置的 AI 后端。
- 默认跳过疑似密钥的文件（`.env`、`*.pem`、`*token*` 等）。
- 仅在有意为之时使用 `--allow-sensitive`。
- 密钥防护是辅助手段，并非保证 —— 分享前请自行检查。
- 见 [docs/SECURITY.md](docs/SECURITY.md)。

## 文档

- [docs/configuration.md](docs/configuration.md) —— `.review-artifact.toml`
- [docs/backends.md](docs/backends.md) —— llm / codex / custom / fake
- [docs/prompts.md](docs/prompts.md) —— prompt 预设
- [docs/SECURITY.md](docs/SECURITY.md) —— 密钥处理

示例：[examples/sample-results](examples/sample-results)（fake 后端演示）、
[examples/fugaku-results.toml](examples/fugaku-results.toml)（HPC 配置示例）。

## 与 runledger 搭配

[runledger](https://github.com/K092203/runledger) 负责捕获 review-artifact 所分诊的运行快照：

```bash
runledger run -- ./solver < input.txt
review-artifact logs runs/latest      # 对快照做只读 AI 分诊
```

## 已知限制

- **`codex`/gpt-5.5 已端到端验证（2026-07-01）；`llm` 后端尚未运行。**
  CI 与自动化测试使用 `fake` 后端（无网络/API）。在真实 `codex` 运行中模型没有捏造引用，
  因此 reject/relocate 路径由确定性测试 + before/after 表格证明，**尚未**由捕获到的真实模型幻觉证明。
- **校验确认的是引用，而非结论。** 一条 finding 可以引用真实的行，却仍得出错误的推断；
  `line_verified` 意味着引用是真实的，而非该 finding 正确。findings 仅供参考。
- **只读的强度取决于后端的 sandbox。** CLI 本身只读取，但无法保证某个后端不会自行采取动作。
- **行校验与 JSON findings 都是 best-effort。** 当评审器的 JSON 无法解析时，
  产物会保留 `raw_output` 且 `findings: []`。

## 非目标（v0.1）

自动修复 / 自动提交、GitHub PR 评论 bot、Web UI / TUI、provider 的完全抽象、安全审计工具化。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。

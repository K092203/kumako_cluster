> 🌐 [English](README.md) · [日本語](README.ja.md) · [中文](README.zh.md)

# runledger

**面向有时间预算的命令行实验的轻量「运行台账（run ledger）」。**
保存完整的运行快照，以 **anytime（随时可停）截断**的方式扫描配置，并始终保留一个
**可随时提交的当前最优解（incumbent）** —— 无需重量级实验平台。

```text
measure -> keep -> compare -> hold the best (in a form you can always submit)
```

- **状态：** v0.1.0（预发布）· MIT · 纯标准库，无运行时依赖
- **已验证：** Python 3.12 / Linux (WSL2) 上 35 项测试通过，2026-06-30 —— 见[验证](#验证)
- **已知限制：** 尚未在 Windows 上验证（见[已知限制](#已知限制)）

---

## 为什么

随手记录的实验日志会消失，而当作业被中止时，你也会丢掉最好的结果。`runledger`
让每一次运行都可被事后检视 —— stdout、stderr、env、git 状态、输入哈希、资源占用 ——
并始终把当前最优解保存在 `state/incumbent.json` 中。即使作业在扫描中途被抢占，
你手里仍有一个可提交的有效结果。

## 与同类工具的区别

| 对比 | runledger 额外提供 |
|----|---------------------|
| **Guild AI / DVC exp** | 截止时间下的 **anytime 截断** + 一个**可提交的 `incumbent.json`** —— 而非「跑完所有 trial 再事后挑最优」。 |
| **hyperfine** | 配置**扫描** + 完整**快照** + **incumbent**，而不仅是计时统计。 |
| **Optuna / GPTune / OpenTuner** | runledger **不是优化器**。LHS/网格/手写配置已经够用；更聪明的搜索引擎只是上游，负责产出 `configs.tsv`。 |

**切入点（wedge）：** 有截止时间、可能被中止的命令行实验，且你必须随时持有一个可提交形式的当前最优解。
面向 HPC 作业运行者、竞赛/优化求解器作者、基准扫描者 —— 而非机器学习实验跟踪。

## 三项保证

1. **Anytime** —— 在每个配置开始前，如果剩余时间无法完成它，扫描会干净地停止，并保留已测得的一切。
2. **可提交的 incumbent** —— `state/incumbent.json` 实时更新，且只会前进到严格更优且 *correct* 的结果。
3. **非零退出码永远不被当作测量值** —— 崩溃和超时绝不会伪装成一个好结果。

三者都在[验证](#验证)中得到演示。

## 安装

```bash
cd runledger
pip install -e ".[dev]"
```

需要 Python 3.11+。无第三方运行时依赖（pytest 仅用于开发）。

## 快速开始

```bash
# 1. 为单次运行保存快照
runledger run --name baseline --timeout 30 -- python3 examples/hello/solver.py --alpha 1.5 --budget 0.1

# 2. 从搜索空间生成配置（拉丁超立方，--seed 可复现）
runledger gen-configs examples/hello/solver.space.tsv --n 8 --seed 1 -o configs.tsv

# 3. 以 anytime 截断扫描；incumbent 实时更新
runledger sweep configs.tsv --budget 1 --elapse 30 --objective max-score -- python3 examples/hello/solver.py

# 4. 从已完成的扫描（重新）计算 incumbent
runledger incumbent sweeps/latest --objective max-score

# 5. 汇总某次 run 或 sweep
runledger summary runs/latest
runledger summary sweeps/latest
```

## 命令

```bash
runledger run -- <cmd>               # 执行并对单条命令保存快照
runledger gen-configs space.tsv      # space.tsv -> configs.tsv (lhs|grid)
runledger sweep configs.tsv -- <cmd> # anytime 扫描：results.csv + 实时 incumbent
runledger incumbent <sweep-dir>      # 针对某目标的最优 correct 配置
runledger summary <run|sweep dir>    # 人类可读的汇总
```

目标（objective）：`max-score`、`min-elapsed`、`score-per-sec`。

## 目录结构

```text
runs/<timestamp>-<name>/   meta.json argv.txt stdout.txt stderr.txt env.txt status.txt resource.json input.sha256
sweeps/<round>/            configs.tsv results.csv errors.log runs/<id>_rep<NNN>/
state/incumbent.json       始终可提交的当前最优解
```

`latest` 是符号链接（在不支持符号链接处回退为 `latest.txt`）。完整 schema 见
[docs/snapshot-format.md](docs/snapshot-format.md) 与
[docs/sweep-format.md](docs/sweep-format.md)。

## 可插拔的测量（`--measure`）

你的程序可以用任意方式报告结果；runledger 通过一个 measure spec 读取它。
**任何方式下，非零退出码都不被信任。**

| 方式 | 如何读取 |
|------|-----|
| `tune-line`（默认） | stderr 末尾的 `#TUNE elapsed=.. score=.. correct=..` 行 |
| `regex` | 对 stdout/stderr 应用正则（在 `[measure]` 表中配置） |
| `json:PATH` | 读取 JSON 文件 `{"elapsed":..,"score":..,"correct":..}` |
| `file:PATH` | 读取纯文本文件，首行为 score |
| `none` | 只用 exit/wall/outcome |

`#TUNE` 只是零配置的默认项 —— **并非**强制要求。见
[docs/measure-spec.md](docs/measure-spec.md)。

## 验证

采集环境：**Python 3.12.3 / Linux 6.18 (WSL2) / 2026-06-30**。

### 测试套件 —— 35 项通过

```text
tests/test_cli.py          3   (错误处理：非法 --measure / --objective)
tests/test_gen_configs.py  6   (space 解析, LHS/网格, pow2 取整, 去重)
tests/test_incumbent.py    6   (目标, 仅 correct, 严格改进, 无回归)
tests/test_measure.py      9   (tune-line/regex/json/file/none, 非零不信任)
tests/test_run.py          7   (成功/失败/超时/缺失二进制, 输入哈希, env 白名单)
tests/test_sweep.py        4   (results 追加, 失败后继续, anytime, 重复 id)
                          ---
                           35   passed in 0.74s
```

### 行为验证（端到端）

| 行为 | 命令 | 观测结果 |
|----------|---------|----------|
| 快照 + 测量 | `runledger run -- … solver.py` | `completed exit=0`；提取到 tune-line 测量值；向 `runs/latest/` 写入 7 个文件 |
| 可复现的扫描 | `gen-configs --seed 1` 然后 `sweep` | incumbent 收敛到 `id=003 score≈1.808`；5 个配置中 4 次实时更新 |
| **anytime 截断** | `sweep --budget 100 --elapse 0.001` | `configs run: 0/1 (stopped early)`；`errors.log`：*anytime cutoff before id 000* |
| **非零不被信任** | 运行先打印 `#TUNE …` 再 `exit 3` | `outcome=failed`；`measure: elapsed=None score=None correct=None` |
| **干净的错误处理** | `--measure bogus` / `--objective bogus` | `error: unknown …` + `exit=2`，无 traceback，未执行任何内容 |
| 经 `--bindir` 的编译型 C++ | `make` → `gen-configs --bin solver` → `sweep --bindir build` | 用 g++ 13.3.0 构建；`8/8` 配置运行；`argv.txt` 解析为 `build/solver`；incumbent `id=000 score=1.95` |

代表性输出：

```console
$ runledger sweep configs.tsv --budget 1 --elapse 30 --objective max-score -- python3 solver.py
sweep: sweeps/round-001
configs run: 5/5  incumbent updates: 4

$ runledger sweep one.tsv --budget 100 --elapse 0.001 -- python3 solver.py
sweep: sweeps/round-002
configs run: 0/1  incumbent updates: 0  (stopped early)
# errors.log: anytime cutoff before id 000: spent=0.0s est=100.0s elapse=0.0s

$ runledger run -- python3 -c "import sys; print('#TUNE score=999 correct=1', file=sys.stderr); sys.exit(3)"
failed  exit=3  wall=0.013s
measure[tune-line]: elapsed=None score=None correct=None    # 非零退出，#TUNE 被忽略
```

编译型示例 [examples/cpp-solver](examples/cpp-solver) 也完成了从构建到扫描的端到端验证（g++ 13.3.0）：

```console
$ make && runledger gen-configs solver.space.tsv --n 8 --seed 1 --bin solver -o configs.tsv
$ runledger sweep configs.tsv --bindir build --budget 1 --elapse 30 --objective max-score
sweep: sweeps/round-001
configs run: 8/8  incumbent updates: 1
$ cat sweeps/latest/runs/000_rep001/argv.txt        # 从 bin 列解析出的二进制
build/solver
--alpha
1.949822
--budget
0.075639
$ runledger incumbent sweeps/latest --objective max-score
incumbent updated: state/incumbent.json
  id=000 objective=max-score score=1.949822 elapsed=0.075639
```

CI 在 Python 3.11 / 3.12 / 3.13 上运行该测试套件。

## 安全

runledger 会记录环境变量（默认白名单）、命令行、stdout/stderr、输入哈希以及 git 状态。
仅在 `--capture-env=all` 时记录全部环境变量；仅在 `--copy-input` 时复制输入内容，
且会跳过疑似密钥的文件。见 [docs/security.md](docs/security.md)。

## 文档

- [docs/snapshot-format.md](docs/snapshot-format.md) —— run 目录 + meta.json
- [docs/sweep-format.md](docs/sweep-format.md) —— configs.tsv、results.csv、incumbent.json、anytime
- [docs/measure-spec.md](docs/measure-spec.md) —— 测量方式
- [docs/hpc.md](docs/hpc.md) —— launcher、ranks/omp、调度器说明
- [docs/security.md](docs/security.md) —— env/密钥处理

示例：[examples/hello](examples/hello)（纯 Python）、
[examples/cpp-solver](examples/cpp-solver)（编译型，`--bindir`）、
[examples/hpc-like](examples/hpc-like)（`--launcher`，ranks/omp）。

## 与 review-artifact 搭配

[review-artifact](https://github.com/K092203/review-artifact) 会对 runledger 产出的快照做只读 AI 分诊：

```bash
runledger run -- ./solver < input.txt
review-artifact logs runs/latest      # 对快照做只读 AI 分诊
```

## 已知限制

- **尚未在 Windows 上验证。** 符号链接 → `latest.txt` 回退以及空 `resource.json` 路径已实现，
  但未在 Windows 上测试；目前请将 Windows 视为 best-effort。
- `resource.json` 为 best-effort：Unix 上是 `getrusage` 差值，其它平台为 `{}`。
  `maxrss` 是峰值水位线，而非精确的逐次运行数值。
- `gen-configs --method grid` 对浮点维度的离散化较粗（lo/mid/hi）；LHS 才是主要方法。

## 非目标（v0.1）

调度器提交适配器（PJM/SLURM/PBS）、Web UI、SQLite 索引、云同步、优化器 / 贝叶斯搜索、GitHub bot。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。

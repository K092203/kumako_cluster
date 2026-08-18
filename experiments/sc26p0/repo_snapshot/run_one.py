#!/usr/bin/env python3
"""1 候補を走らせる。worker から呼ばれる。

   引数: <ens> <cand> <dcost> <budget>

⚠️ 以前は bash スクリプトだったが、本番は Windows で bash も sha256sum も無い。
   worker が動く時点で python は必ずあるので、こちらへ寄せる。
"""
import hashlib, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha16(p):
    if not p.exists():
        return "none"
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


def main():
    if len(sys.argv) != 5:
        sys.exit("usage: run_one.py <ens> <cand> <dcost> <budget>")
    ens, cand, dcost, budget = sys.argv[1:5]

    exe = ".exe" if os.name == "nt" else ""
    solve = HERE / ("solve" + exe)
    if not solve.exists() and (HERE / "solve").exists():
        solve = HERE / "solve"          # 拡張子が付かない環境向け
    inp = HERE / f"input_{ens}.txt"

    # ⚠️ solver は入力を開けなくてもエラーにせず、そのまま壊れた計算を続けて exit 0 する。
    #    failed になるならまだしも、completed で嘘の数字が records に入るのが最悪なので
    #    手前で止める。
    for f in (solve, inp):
        if not f.exists():
            print(f"#RUNERR missing={f.name} cwd={HERE}", file=sys.stderr)
            return 3

    checker = HERE / ("check_revised" + exe)
    if not checker.exists():
        checker = HERE / "check_revised"

    omp = os.environ.get("OMP_NUM_THREADS", "1")
    # ⚠️ ここが cand の唯一の権威ある記録。worker の result.json は job の seed を
    #    落とすので、stderr に出さないと「どの候補だったか」を復元できない。
    print(f"#RUNMETA version=1 ens={ens} cand={cand} dcost={dcost} budget={budget}"
          f" binary_hash={sha16(solve)} checker_hash={sha16(checker)} omp={omp}",
          file=sys.stderr, flush=True)

    env = dict(os.environ)
    env.update({
        "OMP_NUM_THREADS": omp,
        "SC26_PROBE": "1", "SC26_CAND": cand, "SC26_SEL": "1", "SC26_SDC": dcost,
        "SC26_NSWAP": "50", "SC26_POSTKICK": "0", "SC26_PWARM": "0.35",
        "SC26_DECOMP": "0.003",
    })
    return subprocess.run([str(solve), ens, budget], cwd=str(HERE), env=env).returncode


if __name__ == "__main__":
    sys.exit(main())

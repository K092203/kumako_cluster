#!/usr/bin/env python3
"""配った先で最初に走らせる。ビルドして 2 本流し、測定器が動くか見る。

⚠️ Windows / Linux どちらでも動く。bash も sha256sum も使わない。
"""
import os, re, subprocess, sys
from pathlib import Path

SNAP = Path(__file__).resolve().parent / "repo_snapshot"
PY = sys.executable


def run(args, **kw):
    return subprocess.run(args, cwd=str(SNAP), capture_output=True, text=True, **kw)


def main():
    print("== build ==")
    p = run([PY, "setup_build.py"])
    print(p.stdout.strip() or p.stderr.strip())
    if p.returncode != 0:
        print(p.stderr[-3000:], file=sys.stderr)
        sys.exit("ビルドに失敗した。コンパイラを確認すること。")

    print("== smoke (ens 4, cand 1..2, DCOST 12, 60s) ==")
    env = dict(os.environ); env.setdefault("OMP_NUM_THREADS", "1")
    lcs = []
    for c in ("1", "2"):
        p = subprocess.run([PY, "run_one.py", "4", c, "12", "60"],
                           cwd=str(SNAP), capture_output=True, text=True, env=env)
        t = p.stderr
        def pick(pat):
            m = re.findall(pat, t)
            return m[-1] if m else "?"
        lc = pick(r"Lc=([0-9.]+)")
        lcs.append(lc)
        print(f"  cand={c} sw_acc={pick(r'sw_acc=([0-9]+)')} "
              f"E={pick(r'S=3000 E=([0-9.e+-]+)')} Lc={lc} "
              f"censored={pick(r'censored=([0-9])')}")
    for f in SNAP.glob("coord_*.txt"):
        f.unlink()

    if "?" in lcs:
        sys.exit("Lc が取れていない。measurement が動いていない。")
    if len(set(lcs)) == 1:
        print("⚠️ 候補間で Lc が同じ。分解能が足りない (SC26_REFINE を上げる)。")
    print("== ok: censored=0 で Lc が候補ごとに違えば測定器は生きている ==")


if __name__ == "__main__":
    main()

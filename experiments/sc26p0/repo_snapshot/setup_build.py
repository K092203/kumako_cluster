#!/usr/bin/env python3
"""solver と checker をビルドする。Windows(w64devkit) と Linux の両方で動く。

⚠️ cluster_setup.json から呼ばれ、各スロットのローカル複製ごとに 1 回走る。
   コンパイラの場所は環境で違うので、ここで探す。setup_command に固定パスを
   書くと、片方の環境で必ず失敗する。
"""
import os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def candidates():
    root = os.environ.get("SUPERCON_ROOT")
    if root:   # クラスタ標準の同梱ツールチェイン
        yield str(Path(root) / "tools" / "w64devkit" / "bin" / "g++.exe")
    yield "g++"
    yield "c++"


def build(cxx, out, src, extra):
    cmd = [cxx, "-O2", "-std=c++17", *extra, "-o", str(HERE / out), str(HERE / src)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0, p


def main():
    cxx = None
    for c in candidates():
        try:
            if subprocess.run([c, "--version"], capture_output=True).returncode == 0:
                cxx = c
                break
        except OSError:
            continue
    if cxx is None:
        sys.exit("C++ コンパイラが見つからない。%SUPERCON_ROOT%\\tools\\w64devkit\\bin\\g++.exe "
                 "を配置するか、g++ を PATH へ通すこと。")
    print(f"compiler: {cxx}")

    exe = ".exe" if os.name == "nt" else ""
    # ⚠️ worker は OMP_NUM_THREADS=1 を強制する (1スレッド原則) が、
    #    -fopenmp が通るならそのまま使う。通らなければ外して続行する
    #    (1スレッドなら結果は変わらない)。候補比較は同一バイナリ内なので問題ない。
    ok, p = build(cxx, "solve" + exe, "sc26team.cpp", ["-fopenmp", "-DSC26_DEBUG"])
    if not ok:
        print("-fopenmp が通らないので外して再試行する", file=sys.stderr)
        print(p.stderr[-2000:], file=sys.stderr)
        ok, p = build(cxx, "solve" + exe, "sc26team.cpp", ["-DSC26_DEBUG"])
    if not ok:
        print(p.stderr[-4000:], file=sys.stderr)
        sys.exit("solver のビルドに失敗した")

    # checker のビルド失敗は致命的にしない (checker_hash=none になるだけ)
    ok2, p2 = build(cxx, "check_revised" + exe, "check_results_revised_0818.cpp", [])
    if not ok2:
        print("checker のビルドに失敗した (非致命)", file=sys.stderr)
        print(p2.stderr[-1000:], file=sys.stderr)
    print("build ok")


if __name__ == "__main__":
    main()

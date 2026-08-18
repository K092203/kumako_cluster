#!/usr/bin/env python3
"""
gen_configs.py — SC26 P0 研究の config を生成し、make_job.py でジョブを作る

⚠️ 同一 executable 原則: A/B は runtime toggle だけで行う。条件ごとに別ビルドを
   作らない。-Ofast の再結合でコードを足しただけでも最終 L が格子1段ずれるため。

⚠️ 処置量は requested_nswap ではなく **actual_sw_acc**。NSWAP は抽選試行回数であって
   交換回数ではない (legacy 選択則では全8問で実交換ゼロだった)。

Phase:
  0 smoke  … ens 1/4/8 × cand 1..3 × DCOST 4/8/12、短い budget
             見るのは L ではなく「パイプラインが通るか」「候補が分岐するか」
  1 calib  … 全8問 × cand 1..K × DCOST 4/8/12/16
             目的は accepted swap 数の反応曲線。DCOST で処置量を制御できるか
  2 p0     … Phase 1 で処置が入る DCOST を 1〜2 個選び、候補数を増やす

使い方:
  experiments/sc26p0/gen_configs.py --root <cluster_root> --phase 0
  experiments/sc26p0/gen_configs.py --root <cluster_root> --phase 1 --cands 16
  experiments/sc26p0/gen_configs.py --root <cluster_root> --phase 2 --dcost 8 --cands 32
"""
import argparse, hashlib, json, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KUMAKO = HERE.parent.parent


def sha256_file(p):
    import hashlib
    h = hashlib.sha256()
    try:
        h.update(Path(p).read_bytes())
    except OSError:
        return ""
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--phase", type=int, required=True, choices=[0, 1, 2])
    ap.add_argument("--cands", type=int, default=None)
    ap.add_argument("--dcost", type=float, action="append", default=None)
    ap.add_argument("--ens", type=int, action="append", default=None)
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.phase == 0:
        ens_list = args.ens or [1, 4, 8]
        cands = args.cands or 3
        dcosts = args.dcost or [4.0, 8.0, 12.0]
        budget = args.budget or 60
    elif args.phase == 1:
        ens_list = args.ens or list(range(1, 9))
        cands = args.cands or 8
        dcosts = args.dcost or [4.0, 8.0, 12.0, 16.0]
        budget = args.budget or 90
    else:
        ens_list = args.ens or list(range(1, 9))
        cands = args.cands or 32
        dcosts = args.dcost or [8.0]
        budget = args.budget or 120

    snap = Path(args.root) / "repo_snapshot"
    meta = {
        "git_commit": subprocess.run(["git", "-C", str(KUMAKO), "rev-parse", "--short", "HEAD"],
                                     capture_output=True, text=True).stdout.strip(),
        "repo_snapshot_hash": sha256_file(snap / "sc26team.cpp"),
        "checker_version": "revised-0818",
        "experiment_id": f"p{args.phase}_{time.strftime('%Y%m%d-%H%M%S')}",
    }

    total = 0
    for dc in dcosts:
        tag = str(dc).replace(".", "")
        for e in ens_list:
            cmd = [sys.executable, str(KUMAKO / "scripts" / "make_job.py"),
                   "--root", args.root,
                   "--prefix", f"{meta['experiment_id']}_e{e}_d{tag}_",
                   "--start", "1", "--count", str(cands),
                   "--timeout-sec", str(budget + 180),
                   "--artifact", "coord_*.txt",
                   "--param", f"phase={args.phase}",
                   "--param", f"experiment_id={meta['experiment_id']}",
                   "--param", f"ens={e}", "--param", "sel=1",
                   "--param", f"dcost={dc}", "--param", "nswap=50",
                   "--param", f"budget={budget}", "--param", "probe_sweeps=3000",
                   "--param", f"git_commit={meta['git_commit']}",
                   "--param", f"repo_snapshot_hash={meta['repo_snapshot_hash']}",
                   "--param", f"checker_version={meta['checker_version']}",
                   "--",
                   "bash", "./run_one.sh", str(e), "__seed__", str(dc), str(budget)]
            if args.dry_run:
                print(" ".join(cmd))
            else:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            total += cands
        print(f"  DCOST={dc}: ens={ens_list} cand=1..{cands}")

    print(f"\nPhase {args.phase}: 合計 {total} ジョブ  experiment_id={meta['experiment_id']}")
    print(f"  budget={budget}s  → job-秒 ≈ {total*budget}  (280並列なら wall ≈ {total*budget/280/60:.1f} 分)")
    print(f"  git_commit={meta['git_commit']}  repo_snapshot_hash={meta['repo_snapshot_hash']}")
    print("\n⚠️ cand は swap 用 RNG のみを変える。best 状態は同一 (paired 実験)。")
    print("⚠️ 判定は actual_sw_acc で行う。requested_nswap ではない。")


if __name__ == "__main__":
    main()

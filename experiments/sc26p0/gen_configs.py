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


def git_commit_or_none():
    """git が無い / リポジトリでない (ZIP 展開) 場合は None。落とさない。"""
    try:
        r = subprocess.run(["git", "-C", str(KUMAKO), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def snapshot_hash(snap):
    """snapshot の中身そのものから決まる ID。git に依存しない。
    ⚠️ ビルド生成物と実行時の出力は除く (worker ごとに変わるため)。"""
    skip = {"solve", "check_revised"}
    parts = []
    for f in sorted(snap.iterdir()):
        if not f.is_file() or f.name in skip or f.name.startswith("coord_"):
            continue
        parts.append(f"{f.name}:{sha256_file(f)}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--phase", type=int, required=True, choices=[0, 1, 2])
    # ⚠️ budget は「1 スロット 1 スレッド」を前提にした既定値。
    #    probe の深さは wall 時間ではなく計算量で決まる。実測(ens4, DCOST12):
    #      1thread  25s → E=2.9e-10 (完全に解けている。probe に情報が無い)
    #      1thread  60s → E=4.3e-05
    #      1thread 120s → E=6.6e-03
    #      1thread 240s → E=4.3e-02  = 4thread 60s と一致
    #    スロットに複数スレッドを与えるなら、その分だけ budget を減らしてよい。
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
        budget = args.budget or 240
    elif args.phase == 1:
        ens_list = args.ens or list(range(1, 9))
        cands = args.cands or 8
        dcosts = args.dcost or [4.0, 8.0, 12.0, 16.0]
        budget = args.budget or 300
    else:
        ens_list = args.ens or list(range(1, 9))
        cands = args.cands or 32
        dcosts = args.dcost or [8.0]
        budget = args.budget or 360

    snap = Path(args.root) / "repo_snapshot"
    # ⚠️ ディレクトリの有無だけ見ると、worker が作った空の repo_snapshot でも
    #    全ジョブを生成してしまい、bash ./run_one.sh が exit 127 で全部 failed になる。
    need = ["run_one.py", "setup_build.py", "sc26team.cpp", "sc26.h", "cluster_setup.json"]
    if snap.is_dir():
        miss = [f for f in need if not (snap / f).exists()]
        miss += [f"input_{e}.txt" for e in ens_list if not (snap / f"input_{e}.txt").exists()]
        if miss:
            sys.exit(f"repo_snapshot が不完全: {snap}\n  足りない: {', '.join(miss)}")
    if not snap.is_dir():
        sys.exit(f"repo_snapshot が無い: {snap}\n"
                 f"  mkdir -p {snap} && cp experiments/sc26p0/repo_snapshot/* {snap}/\n"
                 f"  (cluster root は全 worker から同じパスで見える場所であること)")
    meta = {
        # ⚠️ GitHub の Download ZIP には .git が無い。git が無くても実験は動くべきなので、
        #    取得できなければ内容ハッシュで代用する。実験の同一性は git commit ではなく
        #    repo_snapshot_hash / binary_hash が担保する。
        "git_commit": git_commit_or_none() or "none(zip)",
        "repo_snapshot_hash": snapshot_hash(snap),
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
                   "python", "run_one.py", str(e), "__seed__", str(dc), str(budget)]
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

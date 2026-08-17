#!/usr/bin/env python3
"""
analyze_seed.py — seed 分布から best-of-k を bootstrap 推定する

なぜ必要か:
    「同じ総計算資源を 1 本の長時間探索に使うべきか、seed を変えて複数
    basin を掘るべきか」を実測で決めるため。multi-start の価値は
    「seed 分布の裾の広さ」で決まる。実際に k 本走らせなくても、
    十分な seed サンプルがあれば min(L_1..L_k) の分布は bootstrap できる。

既存の summarize_results.py との違い:
    summarize は sweep 単位の平均・IQM を出す。ここで欲しいのは
    best-of-k の分布と、次の L 段へ到達する確率。重複実装ではない。

⚠️ 集計から除外するもの (正常値と混ぜない):
    ・correct が false のもの
    ・#TUNE の reject > 0 (独立 validator が出力を拒否した = 異常)
    ・outcome が completed でないもの

使い方:
    experiments/sc26/analyze_seed.py --root <cluster_root>
    experiments/sc26/analyze_seed.py --root <cluster_root> --json out.json
"""
import argparse
import json
import random
import re
import statistics
from collections import defaultdict
from pathlib import Path

TUNE_RE = re.compile(r"#TUNE\s+(.*)")
KS = (1, 2, 3, 4, 6, 8, 12)


def parse_tune(stderr_path):
    """stderr の最後の #TUNE 行を dict にする。無ければ None。"""
    try:
        text = stderr_path.read_text(errors="replace")
    except OSError:
        return None
    last = None
    for m in TUNE_RE.finditer(text):
        last = m.group(1)
    if last is None:
        return None
    out = {}
    for tok in last.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def collect(root):
    """results/*/*/ を走査し、正常な run だけを返す。"""
    rows, skipped = [], defaultdict(int)
    for rj in Path(root, "results").glob("*/*/result.json"):
        try:
            r = json.loads(rj.read_text())
        except (OSError, json.JSONDecodeError):
            skipped["result.json 読めず"] += 1
            continue
        if r.get("outcome") != "completed":
            skipped[f"outcome={r.get('outcome')}"] += 1
            continue
        meas = r.get("measure") or {}
        if not meas.get("correct"):
            skipped["correct=false"] += 1
            continue
        score = meas.get("score")
        if score is None:
            skipped["score なし"] += 1
            continue

        tune = parse_tune(rj.parent / "stderr.txt") or {}
        reject = int(tune.get("reject", 0) or 0)
        if reject > 0:
            # 独立 validator が出力を拒否している。正常値として混ぜてはいけない。
            skipped[f"reject={reject}"] += 1
            continue

        p = r.get("params") or {}
        rows.append({
            "L": -float(score),                      # score = -L なので戻す
            "ens": int(p.get("ens", tune.get("ens", -1))),
            "budget": float(p.get("budget", 0)),
            "dL": float(p.get("dL", 0) or 0),
            "perturb": float(p.get("perturb", tune.get("perturb", 0) or 0)),
            "seed": int(r.get("seed", tune.get("seed", -1) or -1)),
            "phi": float(tune.get("phi", 0) or 0),
            "worker": r.get("worker", ""),
            "wall": r.get("wall_elapsed", 0.0),
        })
    return rows, skipped


def best_of_k(samples, k, trials, rng):
    """seed 分布から k 本抽出して min を取る、を trials 回繰り返す。"""
    n = len(samples)
    return [min(samples[rng.randrange(n)] for _ in range(k)) for _ in range(trials)]


def pct(vals, q):
    s = sorted(vals)
    if not s:
        return float("nan")
    i = min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))
    return s[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--trials", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--json", help="機械可読な出力先")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    rows, skipped = collect(args.root)
    if not rows:
        print("有効な run がありません。--root と results/ を確認してください。")
        if skipped:
            print("除外内訳:", dict(skipped))
        return

    groups = defaultdict(list)
    for r in rows:
        groups[(r["ens"], r["budget"], r["dL"])].append(r)

    report = {"groups": [], "skipped": dict(skipped), "total_ok": len(rows)}

    print(f"有効 run: {len(rows)}   除外: {dict(skipped) if skipped else 'なし'}")
    print()
    hdr = (f"{'ens':>3} {'budget':>7} {'dL':>5} {'n':>4} {'median':>9} {'IQR':>7} "
           f"{'best':>9} " + " ".join(f"{'bo'+str(k):>9}" for k in KS))
    print(hdr)
    print("-" * len(hdr))

    for key in sorted(groups):
        ens, budget, dL = key
        g = groups[key]
        Ls = [r["L"] for r in g]
        med = statistics.median(Ls)
        iqr = pct(Ls, 75) - pct(Ls, 25)
        row = {"ens": ens, "budget": budget, "dL": dL, "n": len(Ls),
               "median": med, "iqr": iqr, "best": min(Ls), "worst": max(Ls),
               "mean": statistics.fmean(Ls),
               "levels": {f"{v:.4f}": Ls.count(v) for v in sorted(set(Ls))},
               "best_of_k": {}}

        cells = []
        for k in KS:
            if k == 1:
                bo = Ls[:]
            else:
                bo = best_of_k(Ls, k, args.trials, rng)
            row["best_of_k"][k] = {
                "expected": statistics.fmean(bo),
                "median": statistics.median(bo),
                "p10": pct(bo, 10), "p25": pct(bo, 25),
                "p75": pct(bo, 75), "p90": pct(bo, 90),
            }
            cells.append(f"{statistics.fmean(bo):9.4f}")

        print(f"{ens:>3} {budget:>7.0f} {dL:>5.2f} {len(Ls):>4} "
              f"{med:>9.4f} {iqr:>7.4f} {min(Ls):>9.4f} " + " ".join(cells))

        # 次の L 段へ到達する確率。best-of-k の理論値 1-(1-p)^k と実測を比べる。
        levels = sorted(set(Ls))
        if len(levels) > 1:
            thr = levels[0]
            p = sum(1 for v in Ls if v <= thr) / len(Ls)
            row["next_level"] = {"threshold": thr, "p_single": p,
                                 "p_theory": {k: 1 - (1 - p) ** k for k in KS}}
        report["groups"].append(row)

    print()
    print("bo<k> = best-of-k の期待値 (seed 分布からの bootstrap)")
    print("⚠️ この結果は Kumako の CPU での測定。**富岳の絶対性能へ外挿してはいけない。**")
    print("   使ってよいのは分布形状・次段到達確率・best-of-k の相対比較のみ。")

    print()
    print("=== 次の L 段への到達確率 ===")
    for row in report["groups"]:
        nl = row.get("next_level")
        if not nl:
            continue
        th = nl["threshold"]
        p = nl["p_single"]
        theo = "  ".join(f"k={k}:{nl['p_theory'][k]:.3f}" for k in (2, 4, 6, 12))
        print(f"  ens{row['ens']} b{row['budget']:.0f} dL{row['dL']:.2f}: "
              f"L<={th:.4f} を1本が引く確率 p={p:.3f} → {theo}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\n機械可読な結果を書き出した: {args.json}")


if __name__ == "__main__":
    main()

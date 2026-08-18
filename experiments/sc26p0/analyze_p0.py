#!/usr/bin/env python3
"""
analyze_p0.py — records.jsonl から P0 を判定し、整合性検査を行う

P0 の問い:
    安価な probe (E_probe) は、その候補の臨界サイズ L_c を予測できるか

期待する符号:
    E_probe が小さい候補ほど L_c も小さい  →  rho(E_probe, L_c) > 0
    (L は小さい方が良い)

⚠️ 全 instance を pool して相関を出さない。**instance ごと**に Spearman を取る。
   問題ごとにエネルギーのスケールが違うため。

⚠️ 整合性検査で落ちたものは相関解析へ送らない。
   「8 候補走らせた」は「8 サンプル取れた」ではない。
"""
import argparse, json, math, random, statistics
from collections import defaultdict
from pathlib import Path


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    if len(xs) < 3:
        return None
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def boot_ci(xs, ys, trials=2000, seed=1):
    rng = random.Random(seed)
    n = len(xs)
    if n < 4:
        return (None, None)
    vals = []
    for _ in range(trials):
        idx = [rng.randrange(n) for _ in range(n)]
        r = spearman([xs[i] for i in idx], [ys[i] for i in idx])
        if r is not None:
            vals.append(r)
    if not vals:
        return (None, None)
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--experiment-id", default=None,
                    help="この experiment_id だけを解析する (指定しなければ全件を群ごとに分けて出す)")
    ap.add_argument("--checkpoint", default="3000")
    args = ap.parse_args()

    recs = [json.loads(l) for l in Path(args.records).read_text().splitlines() if l.strip()]
    n_all = len(recs)
    if args.experiment_id:
        recs = [r for r in recs if r.get("experiment_id") == args.experiment_id]
    out = Path(args.out_dir) if args.out_dir else Path(args.records).parent
    report = {"n_records": len(recs), "n_all_records": n_all,
              "experiment_id_filter": args.experiment_id, "integrity": {}, "groups": []}
    lines = [f"# SC26 P0 解析\n", f"レコード数: {len(recs)} / 全 {n_all}\n"]
    if args.experiment_id:
        lines.append(f"対象 experiment_id: `{args.experiment_id}`\n")

    # ---------- 整合性検査 ----------
    problems = []
    ok = [r for r in recs if r.get("outcome") == "completed"]
    lines.append(f"完走: {len(ok)} / {len(recs)}\n")

    # binary hash が実験群内で揃っているか (同一 executable 原則)
    by_exp = defaultdict(set)
    for r in ok:
        if r.get("binary_hash"):
            by_exp[r.get("experiment_id")].add(r["binary_hash"])
    for exp, hs in by_exp.items():
        if len(hs) > 1:
            problems.append(f"binary_hash が実験群 {exp} 内で不一致: {sorted(hs)}")

    # ⚠️ per-job の sw_acc=0 は異常ではない。**全体で 0** なら instrumentation 異常を疑う
    accs = [r.get("actual_sw_acc") for r in ok if r.get("actual_sw_acc") is not None]
    if accs and max(accs) == 0:
        problems.append("全条件・全候補で actual_sw_acc=0 → swap 経路か計測の異常を疑う")

    report["integrity"]["problems"] = problems
    lines.append("\n## 整合性検査\n")
    lines.append("問題なし\n" if not problems else "".join(f"- ⚠️ {p}\n" for p in problems))

    # ---------- 群ごと ----------
    groups = defaultdict(list)
    for r in ok:
        # ⚠️ 同じ root に Phase 0→1→2 を貯めるので、experiment_id と phase を
        #    跨いで同じ Spearman に混ぜてはいけない (条件も budget も違う)。
        groups[(r.get("experiment_id"), r.get("phase"),
                r.get("ens"), r.get("dcost"))].append(r)

    ck = args.checkpoint
    lines.append(f"\n## 群ごとの結果 (checkpoint S={ck})\n")
    lines.append("| experiment_id | phase | ens | DCOST | n | fp種類 | sw_acc中央 | "
                 "sw_touched中央 | Lc種類 | censored | rho |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|\n")

    for key in sorted(groups, key=lambda k: (str(k[0]), k[1] or 0, k[2] or 0, k[3] or 0)):
        exp, ph, e, dc = key
        g = groups[key]
        fps = {r.get("fp") for r in g if r.get("fp")}
        accs = [r.get("actual_sw_acc") for r in g if r.get("actual_sw_acc") is not None]
        tou = [r.get("sw_touched") for r in g if r.get("sw_touched") is not None]
        lcs = [r.get("Lc") for r in g if r.get("Lc") is not None]
        cens = sum(1 for r in g if (r.get("censored") or 0) != 0)

        # ⚠️ censored は順位相関へ入れない
        pairs = [(r.get(f"E_{ck}"), r.get("Lc")) for r in g
                 if r.get(f"E_{ck}") is not None and r.get("Lc") is not None
                 and (r.get("censored") or 0) == 0]
        rho = spearman([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) >= 3 else None
        lo, hi = boot_ci([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) >= 4 else (None, None)

        # 候補が分岐していない群は診断失敗として除外する
        diag_fail = (len(fps) <= 1 and len(g) > 1) or (len(set(lcs)) <= 1 and len(lcs) > 1)

        lines.append(f"| {exp} | {ph} | {e} | {dc} | {len(g)} | {len(fps)} | "
                     f"{statistics.median(accs) if accs else '-'} | "
                     f"{statistics.median(tou) if tou else '-'} | "
                     f"{len(set(lcs))} | {cens} | "
                     f"{'診断失敗' if diag_fail else (f'{rho:+.3f}' if rho is not None else '-')} |\n")

        report["groups"].append({
            "experiment_id": exp, "phase": ph, "ens": e, "dcost": dc, "n": len(g),
            "fp_unique": len(fps), "lc_unique": len(set(lcs)),
            "sw_acc_median": statistics.median(accs) if accs else None,
            "sw_touched_median": statistics.median(tou) if tou else None,
            "censored": cens, "rho": rho, "rho_ci": [lo, hi],
            "diagnostic_failure": diag_fail,
            "n_used_for_rho": len(pairs),
        })

    # ---------- P0 の総括 ----------
    # ⚠️ 標本が小さいまま判定を出さない。Phase 0 の smoke は 3 候補しかないので、
    #    そのまま計算すると rho=+1.000 が簡単に出て「結論」に見えてしまう。
    MIN_N_PER_GROUP = 8      # 1 群あたり必要な候補数
    MIN_GROUPS      = 4      # 判定に必要な群 (問題×条件) の数
    usable = [g for g in report["groups"]
              if g["rho"] is not None and not g["diagnostic_failure"]
              and g["n_used_for_rho"] >= MIN_N_PER_GROUP]
    rhos = [g["rho"] for g in usable]
    lines.append("\n## P0 総括\n")
    n_small = sum(1 for g in report["groups"]
                  if g["rho"] is not None and not g["diagnostic_failure"]
                  and g["n_used_for_rho"] < MIN_N_PER_GROUP)
    if not rhos or len(rhos) < MIN_GROUPS:
        lines.append(f"⚠️ **判定を出せる標本がない。** "
                     f"条件: 1群 {MIN_N_PER_GROUP} 候補以上 かつ {MIN_GROUPS} 群以上。"
                     f"満たした群 {len(rhos)}、候補数不足の群 {n_small}。\n")
        if n_small:
            lines.append("候補が少ない群の rho は表に出しているが、**結論に使ってはいけない**。"
                         "3 候補なら rho=+1.000 は偶然でも 1/6 の確率で出る。\n")
        lines.append("Phase 0 は測定器の動作確認であって、相関の評価ではない。\n")
        report["verdict"] = "insufficient"
    else:
        med = statistics.median(rhos)
        pos = sum(1 for r in rhos if r > 0)
        lines.append(f"- 評価できた群: {len(rhos)}\n- rho の中央値: **{med:+.3f}**\n"
                     f"- 正の相関だった群: {pos}/{len(rhos)}\n")
        lines.append(f"\n期待する符号は **rho > 0** (E_probe が小さい候補ほど Lc も小さい)。\n")
        report["verdict"] = ("positive" if med > 0.3 and pos >= 0.7 * len(rhos)
                             else "negative" if abs(med) < 0.15 else "inconclusive")
        lines.append(f"\n**判定: {report['verdict']}**\n")
        if report["verdict"] == "negative":
            lines.append("\n⚠️ 候補の分岐と Lc 分解能が十分でこの結果なら、"
                         "E_probe predictor は棄却する。論文で効いたからと positive に解釈しない。\n")

    (out / "summary.md").write_text("".join(lines))
    (out / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print("".join(lines))
    print(f"\n→ {out/'summary.md'} / {out/'summary.json'}")


if __name__ == "__main__":
    main()

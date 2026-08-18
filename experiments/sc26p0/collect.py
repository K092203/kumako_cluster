#!/usr/bin/env python3
"""
collect.py — worker が保存した raw stderr を構造化して records.jsonl / results.csv にする

設計方針:
    worker.py は共有インフラなので**一切変更しない**。worker は stderr.txt を
    全文保存するので、研究固有の診断項目はここで後から解釈する。
    → parser を改善したら stderr.txt から何度でも再生成できる (不変の証拠 + 可変の解釈)。

出力:
    records.jsonl  … 正本。1 行 1 run。情報欠損しにくい
    results.csv    … 派生物 (pandas/Excel 用)

⚠️ hash の意味は分けて扱う:
    git_commit          … 実験コードの版
    repo_snapshot_hash  … worker が同期した snapshot の marker (repo.sha256)
    binary_hash         … 実行された solver バイナリ自体の sha256
    checker_hash        … 判定に使った checker バイナリの sha256
  repo_snapshot_hash を binary_hash と呼ばないこと。

使い方:
    experiments/sc26p0/collect.py --root <cluster_root> [--out-dir <dir>]
"""
import argparse, csv, hashlib, json, re, sys
from pathlib import Path

PARSER_VERSION = 1

# 診断行。solver は各行に version= を出す (schema 追加時の後方互換のため)
RE_KV = re.compile(r"(\w+)=([^\s]+)")


def parse_kv(line):
    return {k: v for k, v in RE_KV.findall(line)}


def num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def config_hash(cfg):
    """実験条件 ID。job_id (再実行 ID) とは別物。
    同じ条件を二重投入したのか再試行なのかを区別できるようにする。"""
    canon = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def parse_run(result_dir):
    """1 つの results/<worker>/<job_id>/ を 1 レコードにする。"""
    rj = result_dir / "result.json"
    if not rj.exists():
        return None
    try:
        r = json.loads(rj.read_text())
    except json.JSONDecodeError:
        return {"job_id": result_dir.name, "parse_error": "result.json decode failed"}

    err = result_dir / "stderr.txt"
    text = err.read_text(errors="replace") if err.exists() else ""

    rec = {
        "parser_version": PARSER_VERSION,
        "job_id": r.get("job_id"),
        "worker": r.get("worker"),
        "sweep_id": r.get("sweep_id"),
        "outcome": r.get("outcome"),
        "exit_code": r.get("exit_code"),
        "wall_elapsed": r.get("wall_elapsed"),
        "finished_at": r.get("finished_at"),
        "error": r.get("error", ""),
    }
    p = r.get("params") or {}
    for k in ("ens", "cand", "sel", "dcost", "nswap", "budget", "probe_sweeps",
              "phase", "experiment_id", "git_commit", "binary_hash",
              "repo_snapshot_hash", "checker_hash", "checker_version"):
        rec[k] = p.get(k)
    rec["requested_nswap"] = p.get("nswap")

    # ---- 診断行 ----
    meta = {}
    for line in text.splitlines():
        if line.startswith("#DIAGMETA"):
            meta = parse_kv(line)
        elif line.startswith("#DIAG "):
            d = parse_kv(line)
            rec["diag_version"] = num(d.get("version"), int)
            for k in ("sweeps", "try_ok", "try_ng", "giveup", "disp_hits",
                      "sw_calls", "sw_try", "sw_acc", "sw_touched"):
                rec[k] = num(d.get(k), int)
            for k in ("relax_sec", "relax_ok_sec", "relax_ng_sec", "valid_sec",
                      "max_trial_disp", "last_imp_t", "idle_after_last_imp",
                      "sweeps_per_sec", "resc_sec"):
                rec[k] = num(d.get(k))
            # ⚠️ 処置量は requested_nswap ではなく actual_sw_acc
            rec["actual_sw_acc"] = rec.get("sw_acc")
        elif line.startswith("#SWAPDIAG"):
            d = parse_kv(line)
            rec["sw_big"] = num(d.get("big"), int)
            rec["sw_small"] = num(d.get("small"), int)
            rec["fp"] = d.get("fp")
        elif line.startswith("#PROBE "):
            d = parse_kv(line)
            s = d.get("S")
            if s is not None:
                rec[f"E_{s}"] = num(d.get("E"))
                rec[f"maxov_{s}"] = num(d.get("max_ov"))
                rec[f"realmaxov_{s}"] = num(d.get("real_max_ov"))
        elif line.startswith("#PROBEEND"):
            d = parse_kv(line)
            for k, cast in (("Lc", float), ("Lc_lo", float), ("Lc_hi", float),
                            ("Lc_res", float), ("phi_c", float),
                            ("censored", int), ("adopted", int), ("probeL", float)):
                rec[k] = num(d.get(k), cast)
        elif line.startswith("#TUNE"):
            d = parse_kv(line)
            rec["final_L"] = num(d.get("L"))
            rec["phi"] = num(d.get("phi"))
            rec["correct"] = d.get("correct")
            rec["elapsed"] = num(d.get("elapsed"))
            rec["reject"] = num(d.get("reject"), int)
    rec["diag_schema"] = num(meta.get("schema"), int)

    rec["config_hash"] = config_hash({
        k: rec.get(k) for k in
        ("ens", "cand", "sel", "dcost", "nswap", "budget", "probe_sweeps", "phase")
    })
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    root = Path(args.root)
    out = Path(args.out_dir) if args.out_dir else root / "sc26p0_out"
    out.mkdir(parents=True, exist_ok=True)

    recs, skipped = [], 0
    for d in sorted((root / "results").glob("*/*")):
        if not d.is_dir():
            continue
        r = parse_run(d)
        if r is None:
            skipped += 1
            continue
        recs.append(r)

    (out / "records.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))

    cols = []
    for r in recs:
        for k in r:
            if k not in cols:
                cols.append(k)
    with (out / "results.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in recs:
            w.writerow(r)

    print(f"records: {len(recs)}  (result.json 無しで飛ばした: {skipped})")
    print(f"  {out/'records.jsonl'}")
    print(f"  {out/'results.csv'}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Local web admin panel for the shared-folder SuperCon cluster."""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import argparse
import io
import itertools
import json
import math
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from flask import Flask, Response, jsonify, request

from make_job import atomic_write_json, coerce_value, existing_job_ids, find_start, sweep_id_for
from optuna_bridge import clamp_numeric, sample_random


WORKER_STATUS_RE = re.compile(r"worker\d+(?:-s\d+)?\.json$")
UNREADABLE = object()
MAX_UPLOAD_ZIP_EXPANDED_BYTES = 50 * 1024 * 1024
SEARCH_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# job_id/prefix はそのまま f"{...}.json" としてファイル名に使われるため、パス区切り文字
# (/ や \)を含む値を拒否する。含めてしまうと jobs/pending/ の外へ書き込まれてしまう。
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ARCHIVE_OUTPUT_RE = re.compile(
    r"archived to (?P<path>\S+): done=(?P<done>\d+) failed=(?P<failed>\d+) result-dirs=(?P<results>\d+)"
)

app = Flask(__name__, static_folder=None, template_folder=None)

# The panel may receive concurrent requests because Flask runs with threaded=True.
# Keep completed processes too: their exit state is useful to the search list.
_search_processes: dict[str, subprocess.Popen[Any]] = {}
_search_processes_lock = threading.Lock()
_dashboard_processes: dict[str, tuple[subprocess.Popen[Any], int]] = {}
_dashboard_processes_lock = threading.Lock()


def default_root() -> Path:
    """Return the repository root, following the other scripts' convention."""
    return Path(__file__).resolve().parents[1]


app.config["CLUSTER_ROOT"] = default_root()


def cluster_root() -> Path:
    return Path(app.config["CLUSTER_ROOT"])


def parse_time(text: str) -> datetime | None:
    """Parse a status timestamp using the same rule as scripts/status.py."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).astimezone()
    except Exception:
        return None


def json_files(directory: Path) -> Iterator[Path]:
    """Yield JSON files while treating an unavailable directory as empty."""
    try:
        yield from sorted(directory.glob("*.json"))
    except Exception:
        return


def read_json(path: Path) -> Any:
    """Read one JSON file, returning a sentinel when it cannot be used."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return UNREADABLE


def status_group(filename: str) -> str | None:
    """Classify a status-file name into an API response group."""
    if filename.startswith("bridge-"):
        return "bridges"
    if filename.endswith("-supervisor.json"):
        return "supervisors"
    if filename.endswith("-launcher.json"):
        return "launchers"
    if WORKER_STATUS_RE.fullmatch(filename):
        return "workers"
    return None


def collect_status(root: Path, stale_sec: int = 60) -> dict[str, list[dict[str, Any]]]:
    """Read status files and group them for the status API."""
    groups: dict[str, list[dict[str, Any]]] = {
        "workers": [],
        "supervisors": [],
        "launchers": [],
        "bridges": [],
    }
    now = datetime.now(timezone.utc).astimezone()

    for path in json_files(root / "status"):
        group = status_group(path.name)
        if group is None:
            continue
        data = read_json(path)
        if not isinstance(data, dict):
            continue

        updated_at = str(data.get("updated_at", ""))
        updated = parse_time(updated_at)
        age_sec = int((now - updated).total_seconds()) if updated else None
        status = str(data.get("status", ""))
        if age_sec is not None and age_sec > stale_sec:
            status = "stale"

        groups[group].append(
            {
                "worker": str(data.get("worker", path.stem)),
                "status": status,
                "current_job": data.get("current_job"),
                "message": str(data.get("message", "")),
                "updated_at": updated_at,
                "age_sec": age_sec,
            }
        )

    for rows in groups.values():
        rows.sort(key=lambda row: row["worker"])
    return groups


def count_json_files(directory: Path) -> int:
    """Count readable directory entries with a JSON extension."""
    return sum(1 for _ in json_files(directory))


def collect_incumbent(root: Path) -> dict[str, Any]:
    """Read incumbent and search best-result files without failing the endpoint."""
    incumbent = read_json(root / "state" / "incumbent.json")
    if incumbent is UNREADABLE:
        incumbent = None
    search: dict[str, Any] = {}
    for path in json_files(root / "state" / "search"):
        if not path.name.endswith(".best.json"):
            continue
        data = read_json(path)
        if data is UNREADABLE:
            continue
        search[path.stem.removesuffix(".best")] = data
    return {"job": incumbent, "search": search}


def now_iso() -> str:
    """Return timestamps in the job-file format used by the other scripts."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def estimate_eta(root: Path, count: int, timeout_sec: float, sweep_id: str | None = None) -> dict[str, Any]:
    """Estimate completion time from active workers and matching past results."""
    slots = max(1, sum(worker["status"] != "stale" for worker in collect_status(root)["workers"]))
    elapsed: list[float] = []
    if sweep_id is not None:
        try:
            result_paths = (root / "results").glob("*/*/result.json")
            for path in result_paths:
                data = read_json(path)
                value = data.get("wall_elapsed") if isinstance(data, dict) and data.get("sweep_id") == sweep_id else None
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    elapsed.append(float(value))
        except OSError:
            pass
    if elapsed:
        seconds = sum(elapsed) / len(elapsed)
        basis = "measured"
    else:
        seconds = timeout_sec
        basis = "timeout"
    return {
        "eta_seconds": math.ceil(count / slots) * seconds,
        "basis": basis,
        "slots": slots,
        "seconds_per_job": seconds,
    }


def sweep_error(message: str) -> tuple[Response, int]:
    return jsonify({"error": message}), 400


def build_search_launch(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a panel search request and return its spec and bridge options."""
    if not isinstance(payload, dict):
        raise ValueError("JSON オブジェクトを送信してください。")

    name = payload.get("name")
    if not isinstance(name, str) or not name or not SEARCH_NAME_RE.fullmatch(name):
        raise ValueError("name は英数字・ハイフン・アンダースコアのみの空でない文字列にしてください。")

    command = payload.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("command は空でない文字列配列にしてください。")

    params = validate_params_spec(payload.get("params"))
    if not params:
        raise ValueError("paramsを1つ以上指定してください")

    instances = payload.get("instances")
    if (
        not isinstance(instances, list)
        or not instances
        or not all(isinstance(instance, int) and not isinstance(instance, bool) for instance in instances)
    ):
        raise ValueError("instancesを1つ以上指定してください")

    timeout_sec = payload.get("timeout_sec")
    if (
        isinstance(timeout_sec, bool)
        or not isinstance(timeout_sec, (int, float))
        or not math.isfinite(timeout_sec)
        or timeout_sec <= 0
    ):
        raise ValueError("timeout_sec は 0 より大きい数値にしてください。")

    max_trials = payload.get("max_trials")
    if not isinstance(max_trials, int) or isinstance(max_trials, bool) or max_trials < 1:
        raise ValueError("max_trials は 1 以上の整数にしてください。")
    parallel = payload.get("parallel")
    if not isinstance(parallel, int) or isinstance(parallel, bool) or parallel < 1:
        raise ValueError("parallel は 1 以上の整数にしてください。")
    agg = payload.get("agg")
    if agg not in {"mean", "min", "max"}:
        raise ValueError("agg は mean / min / max のいずれかです。")
    direction = payload.get("direction")
    if direction not in {"max", "min"}:
        raise ValueError("direction は max / min のいずれかです。")

    return (
        {
            "name": name,
            "params": params,
            "command": command,
            "instances": instances,
            "timeout_sec": float(timeout_sec),
        },
        {"max_trials": max_trials, "parallel": parallel, "agg": agg, "direction": direction},
    )


def numeric_value(value: Any, name: str, field: str) -> float:
    """Accept JSON numbers (and simple form-like strings) as finite numeric values."""
    if isinstance(value, bool):
        raise ValueError(f"パラメータ {name}: {field} は数値にしてください。")
    converted = coerce_value(str(value))
    if not isinstance(converted, (int, float)) or isinstance(converted, bool) or not math.isfinite(converted):
        raise ValueError(f"パラメータ {name}: {field} は有限の数値にしてください。")
    return float(converted)


def validate_params_spec(value: Any) -> dict[str, dict[str, Any]]:
    """Validate the compact parameter-spec shape accepted by optuna_bridge."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("params はオブジェクトにしてください。")

    result: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError("パラメータ名は空でない文字列にしてください。")
        if not isinstance(raw, dict):
            raise ValueError(f"パラメータ {name}: 定義はオブジェクトにしてください。")
        kind = raw.get("type")
        if kind not in {"float", "int", "cat"}:
            raise ValueError(f"パラメータ {name}: type は float / int / cat のいずれかです。")
        p = dict(raw)
        if kind == "cat":
            choices = p.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError(f"パラメータ {name}: cat には空でない choices が必要です。")
        else:
            if "low" not in p or "high" not in p:
                raise ValueError(f"パラメータ {name}: low と high が必要です。")
            low = numeric_value(p["low"], name, "low")
            high = numeric_value(p["high"], name, "high")
            if low >= high:
                raise ValueError(f"パラメータ {name}: low は high より小さくしてください。")
            if p.get("log") and low <= 0:
                raise ValueError(f"パラメータ {name}: log 指定では low を 0 より大きくしてください。")
            p["low"] = low
            p["high"] = high
            p["log"] = bool(p.get("log", False))
        result[name] = p
    return result


def grid_values(p: dict[str, Any], levels: int) -> list[object]:
    """Build one grid axis, delegating integer rounding/clamping to optuna_bridge."""
    if p["type"] == "cat":
        return list(p["choices"])
    if p.get("log"):
        values = [math.exp(math.log(p["low"]) + (math.log(p["high"]) - math.log(p["low"])) * i / (levels - 1)) for i in range(levels)]
    else:
        values = [p["low"] + (p["high"] - p["low"]) * i / (levels - 1) for i in range(levels)]
    return [clamp_numeric(item, p) for item in values]


def build_sweep(payload: Any, root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate a sweep request and return its normalized inputs and job previews."""
    if not isinstance(payload, dict):
        raise ValueError("JSON オブジェクトを送信してください。")
    command = payload.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("command は空でない文字列配列にしてください。")
    count = payload.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count は 1 以上の整数にしてください。")
    sampling = payload.get("sampling", "random")
    if sampling not in {"random", "grid"}:
        raise ValueError("sampling は random または grid にしてください。")
    timeout = payload.get("timeout_sec", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_sec は 0 より大きい数値にしてください。")
    prefix = payload.get("prefix", "sweep")
    if not isinstance(prefix, str) or not JOB_ID_RE.fullmatch(prefix):
        raise ValueError("prefix は英数字・._- のみの空でない文字列にしてください。")
    artifacts = payload.get("artifacts", [])
    if artifacts is None:
        artifacts = []
    if not isinstance(artifacts, list) or not all(isinstance(item, str) and item for item in artifacts):
        raise ValueError("artifacts は文字列配列にしてください。")

    params_spec = validate_params_spec(payload.get("params", {}))
    # ランダムサンプリングでは、プレビューで見せた値と実際にsubmitされる値が
    # 一致するよう、シードを解決してレスポンスに含める(呼び出し元がそのシードを
    # 次のリクエストに含めれば、同じ乱数列が再現される)。
    resolved_seed = payload.get("seed")
    if not params_spec:
        parameter_sets = [{} for _ in range(count)]
    elif sampling == "random":
        if resolved_seed is None:
            resolved_seed = random.SystemRandom().randrange(2**31)
        try:
            rng = random.Random(resolved_seed)
        except (TypeError, ValueError) as error:
            raise ValueError("seed は Random で使用できる値にしてください。") from error
        parameter_sets = []
        for _ in range(count):
            sampled = sample_random(params_spec, rng)
            parameter_sets.append(
                {
                    name: clamp_numeric(sampled[name], p) if p["type"] in {"float", "int"} else sampled[name]
                    for name, p in params_spec.items()
                }
            )
    else:
        numeric_count = sum(p["type"] in {"float", "int"} for p in params_spec.values())
        levels = max(2, round(count ** (1 / max(1, numeric_count))))
        axes = [grid_values(p, levels) for p in params_spec.values()]
        total = math.prod(len(axis) for axis in axes)
        if total > 2000:
            raise ValueError("グリッドの組み合わせが 2000 件を超えます。範囲か件数を減らしてください。")
        names = list(params_spec)
        parameter_sets = [dict(zip(names, values)) for values in itertools.product(*axes)]

    used = existing_job_ids(root)
    start = find_start(prefix, len(parameter_sets), used)
    jobs = [
        {
            "job_id": f"{prefix}{start + offset:03d}",
            "params": params,
            "sweep_id": sweep_id_for(params),
        }
        for offset, params in enumerate(parameter_sets)
    ]
    return {"command": command, "timeout_sec": float(timeout), "artifacts": artifacts, "resolved_seed": resolved_seed}, jobs


def upload_timeout_sec(job: dict[str, Any]) -> float:
    """Return a safe timeout estimate without changing the uploaded job."""
    value = job.get("timeout_sec", 30)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return float(value)
    return 30.0


def validate_uploaded_zip(contents: bytes, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Validate ZIP members and retain the original JSON objects for a later commit."""
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    existing = existing_job_ids(root)
    # job_id -> このZIP内で実際に採用された(=accepted済みの)ファイル名。
    # 「重複」は採用済みのjob_idとだけ比較する。不正なエントリはjob_idを消費しない
    # (壊れた1件のせいで、後続の正しい同名エントリがブロックされないようにするため)。
    accepted_by_id: dict[str, str] = {}

    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        total_size = 0
        for info in archive.infolist():
            total_size += info.file_size
            if total_size > MAX_UPLOAD_ZIP_EXPANDED_BYTES:
                raise ValueError("ZIP の展開後サイズが 50MB を超えています。")

        for info in archive.infolist():
            filename = info.filename
            if info.is_dir() or Path(filename).suffix != ".json":
                skipped.append({"filename": filename, "reason": "対象外のファイル"})
                continue
            try:
                data = json.loads(archive.read(info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                skipped.append({"filename": filename, "reason": "不正なJSON"})
                continue
            if not isinstance(data, dict):
                skipped.append({"filename": filename, "reason": "不正なJSON"})
                continue

            job_id = data.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                skipped.append({"filename": filename, "reason": "job_id が指定されていません"})
                continue
            if not JOB_ID_RE.fullmatch(job_id):
                skipped.append({"filename": filename, "reason": "job_id に使用できない文字が含まれています(英数字・._- のみ)"})
                continue
            if job_id in accepted_by_id:
                winner = accepted_by_id[job_id]
                skipped.append({"filename": filename, "reason": f"ZIP内でjob_idが重複しています({winner} を採用済み)"})
                continue
            if "command" not in data or not isinstance(data["command"], (str, list)):
                skipped.append({"filename": filename, "reason": "command が不正です"})
                continue
            if job_id in existing:
                skipped.append({"filename": filename, "reason": "クラスタに同名のジョブが既にあります"})
                continue

            accepted_by_id[job_id] = filename
            accepted.append(
                {
                    "job_id": job_id,
                    "filename": filename,
                    "timeout_sec": upload_timeout_sec(data),
                    "command": data["command"],
                    "data": data,
                }
            )
    return accepted, skipped


def uploaded_zip_or_error() -> tuple[list[dict[str, Any]], list[dict[str, str]]] | tuple[Response, int]:
    """Read the multipart upload and return the shared ZIP validation result."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return sweep_error("ZIP ファイルを file フィールドで指定してください。")
    try:
        return validate_uploaded_zip(upload.read(), cluster_root())
    except zipfile.BadZipFile:
        return sweep_error("ZIP ファイルを読み込めません。壊れていないか確認してください。")
    except ValueError as error:
        return sweep_error(str(error))


@app.get("/")
def index() -> Response:
    try:
        html = (Path(__file__).resolve().parent / "admin_panel_web" / "index.html").read_text(encoding="utf-8")
    except Exception:
        return Response("admin panel web files are unavailable", status=500, mimetype="text/plain")
    return Response(html, mimetype="text/html")


@app.get("/api/status")
def api_status() -> Response:
    return jsonify(collect_status(cluster_root()))


@app.get("/api/queue")
def api_queue() -> Response:
    jobs = cluster_root() / "jobs"
    return jsonify(
        {
            "pending": count_json_files(jobs / "pending"),
            "running": count_json_files(jobs / "running"),
            "done": count_json_files(jobs / "done"),
            "failed": count_json_files(jobs / "failed"),
        }
    )


@app.get("/api/incumbent")
def api_incumbent() -> Response:
    return jsonify(collect_incumbent(cluster_root()))


def template_request_or_error(payload: Any, *, require_payload: bool) -> tuple[str, str, dict[str, Any]] | tuple[Response, int]:
    """Validate the common fields used to name panel templates."""
    if not isinstance(payload, dict):
        return sweep_error("JSON オブジェクトを送信してください。")
    kind = payload.get("kind")
    if kind not in {"sweep", "search"}:
        return sweep_error("kind は sweep または search にしてください。")
    name = payload.get("name")
    if not isinstance(name, str) or not name or not SEARCH_NAME_RE.fullmatch(name):
        return sweep_error("name は英数字・ハイフン・アンダースコアのみにしてください")
    template_payload = payload.get("payload")
    if require_payload and not isinstance(template_payload, dict):
        return sweep_error("payload はオブジェクトにしてください")
    return kind, name, template_payload if isinstance(template_payload, dict) else {}


@app.post("/api/templates/save")
def api_templates_save() -> Response | tuple[Response, int]:
    result = template_request_or_error(request.get_json(silent=True), require_payload=True)
    if isinstance(result[0], Response):
        return result
    kind, name, payload = result
    path = cluster_root() / "state" / "panel_templates" / f"{kind}-{name}.json"
    atomic_write_json(path, {"kind": kind, "name": name, "payload": payload, "saved_at": now_iso()})
    return jsonify({"kind": kind, "name": name, "saved": True})


@app.get("/api/templates/list")
def api_templates_list() -> Response:
    templates: list[dict[str, Any]] = []
    for path in json_files(cluster_root() / "state" / "panel_templates"):
        data = read_json(path)
        if (
            not isinstance(data, dict)
            or data.get("kind") not in {"sweep", "search"}
            or not isinstance(data.get("name"), str)
            or not SEARCH_NAME_RE.fullmatch(data["name"])
            or not isinstance(data.get("payload"), dict)
        ):
            continue
        templates.append(
            {
                "kind": data["kind"],
                "name": data["name"],
                "payload": data["payload"],
                "saved_at": data.get("saved_at", ""),
            }
        )
    templates.sort(key=lambda template: (template["kind"], template["name"]))
    return jsonify({"templates": templates})


@app.post("/api/templates/delete")
def api_templates_delete() -> Response | tuple[Response, int]:
    result = template_request_or_error(request.get_json(silent=True), require_payload=False)
    if isinstance(result[0], Response):
        return result
    kind, name, _ = result
    path = cluster_root() / "state" / "panel_templates" / f"{kind}-{name}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        return jsonify({"error": "テンプレートが見つかりません。"}), 404
    except OSError as error:
        return jsonify({"error": f"テンプレートを削除できません: {error}"}), 500
    return jsonify({"deleted": True})


@app.post("/api/search/launch")
def api_search_launch() -> Response | tuple[Response, int]:
    payload = request.get_json(silent=True)
    try:
        spec, options = build_search_launch(payload)
    except ValueError as error:
        return sweep_error(str(error))

    root = cluster_root()
    name = spec["name"]
    spec_path = root / "state" / "panel_specs" / f"{name}.json"
    with _search_processes_lock:
        existing = _search_processes.get(name)
        if existing is not None and existing.poll() is None:
            return sweep_error("同名の探索が既に実行中です")

        atomic_write_json(spec_path, spec)
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(root / "scripts" / "optuna_bridge.py"),
                    "--spec",
                    str(spec_path),
                    "--root",
                    str(root),
                    "--max-trials",
                    str(options["max_trials"]),
                    "--parallel",
                    str(options["parallel"]),
                    "--agg",
                    options["agg"],
                    "--direction",
                    options["direction"],
                ],
                cwd=str(root),
            )
        except OSError as error:
            return jsonify({"error": f"探索プロセスを起動できません: {error}"}), 500
        _search_processes[name] = proc

    return jsonify({"name": name, "started": True, "spec_path": str(spec_path.relative_to(root))})


@app.post("/api/search/stop")
def api_search_stop() -> Response | tuple[Response, int]:
    payload = request.get_json(silent=True)
    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(name, str):
        return sweep_error("name は文字列にしてください。")

    with _search_processes_lock:
        proc = _search_processes.get(name)
        if proc is None:
            return jsonify({"error": "指定された探索はこのパネルで追跡されていません。"}), 404
        if proc.poll() is None:
            proc.terminate()
    return jsonify({"name": name, "stopped": True})


@app.post("/api/archive")
def api_archive() -> Response | tuple[Response, int]:
    payload = request.get_json(silent=True)
    label = payload.get("label") if isinstance(payload, dict) else None
    if label is not None and not isinstance(label, str):
        return sweep_error("label は文字列にしてください。")

    root = cluster_root()
    command = [sys.executable, str(root / "scripts" / "archive_results.py"), "--root", str(root)]
    if label is not None:
        command.extend(["--label", label])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "アーカイブ処理が120秒以内に完了しませんでした。"}), 400
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        return jsonify({"error": f"アーカイブ処理に失敗しました: {details}"}), 400

    match = ARCHIVE_OUTPUT_RE.search(result.stdout)
    if match is None:
        return jsonify({"raw_output": result.stdout + result.stderr})
    return jsonify(
        {
            "archived_to": match.group("path"),
            "done": int(match.group("done")),
            "failed": int(match.group("failed")),
            "result_dirs": int(match.group("results")),
        }
    )


def wait_for_port(port: int, proc: "subprocess.Popen[Any]", timeout: float = 5.0) -> bool:
    """Block until the process accepts a connection, exits, or the timeout passes.

    window.open() on the frontend fires as soon as this endpoint responds, so
    without this wait the popup can race the dashboard subprocess's own startup
    and land on a bare connection-refused error page.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.15)
    return proc.poll() is None


@app.post("/api/optuna-dashboard/launch")
def api_optuna_dashboard_launch() -> Response | tuple[Response, int]:
    payload = request.get_json(silent=True)
    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not name or not SEARCH_NAME_RE.fullmatch(name):
        return sweep_error("name は英数字・ハイフン・アンダースコアのみの空でない文字列にしてください。")

    root = cluster_root()
    journal_path = root / "state" / "search" / f"{name}.journal.log"
    if not journal_path.exists():
        return sweep_error(
            "このsearchのjournalログが見つかりません(Optunaエンジンを使った探索のみ対応、または探索がまだ何も実行していません)"
        )

    with _dashboard_processes_lock:
        existing = _dashboard_processes.get(name)
        if existing is not None and existing[0].poll() is None:
            _, port = existing
            return jsonify({"name": name, "port": port, "reused": True, "url": f"http://127.0.0.1:{port}/"})

        if shutil.which("optuna-dashboard") is None:
            return sweep_error("optuna-dashboard が見つかりません。`pip install optuna-dashboard` を実行してからもう一度試してください。")

        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        try:
            proc = subprocess.Popen(
                ["optuna-dashboard", str(journal_path), "--port", str(port), "--host", "127.0.0.1"]
            )
        except OSError as error:
            return jsonify({"error": f"optuna-dashboard を起動できません: {error}"}), 500
        _dashboard_processes[name] = (proc, port)

    if not wait_for_port(port, proc):
        return jsonify(
            {"error": "optuna-dashboard の起動に失敗しました(このパネルのコンソール出力を確認してください)。"}
        ), 500
    return jsonify({"name": name, "port": port, "reused": False, "url": f"http://127.0.0.1:{port}/"})


@app.get("/api/search/list")
def api_search_list() -> Response:
    root = cluster_root()
    names = {path.stem for path in json_files(root / "state" / "panel_specs")}
    with _search_processes_lock:
        stoppable = {
            name: proc.poll() is None
            for name, proc in _search_processes.items()
        }

    searches: list[dict[str, Any]] = []
    for name in sorted(names):
        progress = read_json(root / "status" / f"bridge-{name}.json")
        best = read_json(root / "state" / "search" / f"{name}.best.json")
        searches.append(
            {
                "name": name,
                "stoppable": stoppable.get(name, False),
                "progress": None if progress is UNREADABLE else progress,
                "best": None if best is UNREADABLE else best,
                "has_journal": (root / "state" / "search" / f"{name}.journal.log").exists(),
            }
        )
    return jsonify({"searches": searches})


@app.post("/api/sweep/preview")
def api_sweep_preview() -> Response | tuple[Response, int]:
    payload = request.get_json(silent=True)
    try:
        options, jobs = build_sweep(payload, cluster_root())
    except ValueError as error:
        return sweep_error(str(error))
    first_sweep_id = jobs[0]["sweep_id"] if jobs else None
    return jsonify(
        {
            "jobs": jobs,
            "requested_count": payload["count"],
            "actual_count": len(jobs),
            "seed": options["resolved_seed"],
            "eta": estimate_eta(cluster_root(), len(jobs), options["timeout_sec"], first_sweep_id),
        }
    )


@app.post("/api/sweep/submit")
def api_sweep_submit() -> Response | tuple[Response, int]:
    payload = request.get_json(silent=True)
    try:
        options, jobs = build_sweep(payload, cluster_root())
    except ValueError as error:
        return sweep_error(str(error))

    pending = cluster_root() / "jobs" / "pending"
    created: list[str] = []
    for seed, preview in enumerate(jobs, start=1):
        job = {
            "job_id": preview["job_id"],
            "command": options["command"],
            "params": preview["params"],
            "sweep_id": preview["sweep_id"],
            "seed": seed,
            "timeout_sec": options["timeout_sec"],
            "created_at": now_iso(),
        }
        if options["artifacts"]:
            job["artifacts"] = options["artifacts"]
        atomic_write_json(pending / f"{preview['job_id']}.json", job)
        created.append(preview["job_id"])
    return jsonify({"created": created, "count": len(created)})


@app.post("/api/jobs/upload-zip/preview")
def api_upload_zip_preview() -> Response | tuple[Response, int]:
    result = uploaded_zip_or_error()
    if isinstance(result[0], Response):
        return result
    accepted, skipped = result
    timeout_sec = sum(job["timeout_sec"] for job in accepted) / len(accepted) if accepted else 30.0
    return jsonify(
        {
            "accepted": [
                {key: job[key] for key in ("job_id", "filename", "timeout_sec")}
                for job in accepted
            ],
            "skipped": skipped,
            "eta": estimate_eta(cluster_root(), len(accepted), timeout_sec),
        }
    )


@app.post("/api/jobs/upload-zip/commit")
def api_upload_zip_commit() -> Response | tuple[Response, int]:
    result = uploaded_zip_or_error()
    if isinstance(result[0], Response):
        return result
    accepted, skipped = result
    pending = cluster_root() / "jobs" / "pending"
    created: list[str] = []
    for job in accepted:
        data = job["data"]
        if "created_at" not in data:
            data["created_at"] = now_iso()
        atomic_write_json(pending / f"{job['job_id']}.json", data)
        created.append(job["job_id"])
    return jsonify({"created": created, "skipped_count": len(skipped)})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local SuperCon admin panel.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    app.config["CLUSTER_ROOT"] = args.root
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

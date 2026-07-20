"""Regression tests for the Flask admin panel APIs."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("flask")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import admin_panel
import worker


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    worker.ensure_layout(tmp_path)
    return tmp_path


@pytest.fixture()
def client(root: Path):
    """Use an isolated cluster root and reset panel-global process state."""
    original_root = admin_panel.app.config["CLUSTER_ROOT"]
    original_testing = admin_panel.app.config.get("TESTING")
    admin_panel.app.config.update(CLUSTER_ROOT=root, TESTING=True)
    with admin_panel._search_processes_lock:
        admin_panel._search_processes.clear()
    with admin_panel._dashboard_processes_lock:
        admin_panel._dashboard_processes.clear()

    try:
        with admin_panel.app.test_client() as test_client:
            yield test_client
    finally:
        with admin_panel._search_processes_lock:
            admin_panel._search_processes.clear()
        with admin_panel._dashboard_processes_lock:
            admin_panel._dashboard_processes.clear()
        admin_panel.app.config.update(CLUSTER_ROOT=original_root, TESTING=original_testing)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_zip(members: list[tuple[str, bytes]]) -> io.BytesIO:
    contents = io.BytesIO()
    with zipfile.ZipFile(contents, "w") as archive:
        for filename, data in members:
            archive.writestr(filename, data)
    contents.seek(0)
    return contents


def sweep_payload(**overrides) -> dict:
    payload = {
        "command": ["python", "solver.py"],
        "count": 3,
        "sampling": "random",
        "timeout_sec": 30,
        "prefix": "range",
        "params": {
            "alpha": {"type": "float", "low": 0.1, "high": 0.9},
            "steps": {"type": "int", "low": 2, "high": 8},
            "mode": {"type": "cat", "choices": ["fast", "safe"]},
        },
    }
    payload.update(overrides)
    return payload


def search_payload(**overrides) -> dict:
    payload = {
        "name": "trial",
        "command": ["python", "solver.py"],
        "params": {"alpha": {"type": "float", "low": 0.0, "high": 1.0}},
        "instances": [1, 2],
        "timeout_sec": 30,
        "max_trials": 12,
        "parallel": 3,
        "agg": "mean",
        "direction": "max",
    }
    payload.update(overrides)
    return payload


def test_status_groups_workers_supervisors_and_bridges_and_marks_stale(root: Path, client) -> None:
    fresh = datetime.now(timezone.utc).astimezone().isoformat()
    stale = (datetime.now(timezone.utc).astimezone() - timedelta(seconds=61)).isoformat()
    write_json(root / "status" / "worker01.json", {"worker": "worker01", "status": "running", "updated_at": fresh})
    write_json(root / "status" / "worker02.json", {"worker": "worker02", "status": "idle", "updated_at": stale})
    write_json(root / "status" / "worker01-supervisor.json", {"worker": "supervisor01", "status": "running", "updated_at": fresh})
    write_json(root / "status" / "bridge-study.json", {"worker": "study", "status": "running", "updated_at": fresh})

    response = client.get("/api/status")

    assert response.status_code == 200
    data = response.get_json()
    assert [row["worker"] for row in data["workers"]] == ["worker01", "worker02"]
    assert data["workers"][0]["status"] == "running"
    assert data["workers"][1]["status"] == "stale"
    assert len(data["supervisors"]) == 1
    assert data["supervisors"][0]["worker"] == "supervisor01"
    assert data["bridges"][0]["worker"] == "study"


def test_queue_counts_json_files_in_each_bucket(root: Path, client) -> None:
    for bucket, count in {"pending": 2, "running": 1, "done": 3, "failed": 1}.items():
        for index in range(count):
            write_json(root / "jobs" / bucket / f"job-{index}.json", {"job_id": f"{bucket}-{index}"})
    (root / "jobs" / "pending" / "ignored.txt").write_text("not a job", encoding="utf-8")

    response = client.get("/api/queue")

    assert response.status_code == 200
    assert response.get_json() == {"pending": 2, "running": 1, "done": 3, "failed": 1}


def test_queue_pending_reports_dwell_time_oldest_first(root: Path, client) -> None:
    old_created = (datetime.now(timezone.utc).astimezone() - timedelta(seconds=900)).isoformat()
    new_created = datetime.now(timezone.utc).astimezone().isoformat()
    write_json(root / "jobs" / "pending" / "old.json", {"job_id": "old", "created_at": old_created})
    write_json(root / "jobs" / "pending" / "new.json", {"job_id": "new", "created_at": new_created})

    response = client.get("/api/queue/pending")

    assert response.status_code == 200
    data = response.get_json()
    assert data["total"] == 2
    assert [job["job_id"] for job in data["jobs"]] == ["old", "new"]
    assert data["jobs"][0]["age_sec"] >= 900
    assert data["jobs"][1]["age_sec"] < 900
    assert data["warn_after_sec"] == admin_panel.PENDING_DWELL_WARN_SEC


def test_queue_pending_does_not_fabricate_age_when_created_at_missing(root: Path, client) -> None:
    write_json(root / "jobs" / "pending" / "no-timestamp.json", {"job_id": "no-timestamp"})

    response = client.get("/api/queue/pending")

    assert response.status_code == 200
    job = response.get_json()["jobs"][0]
    assert job["age_sec"] is None
    assert job["created_at"] is None


def test_queue_pending_sorts_missing_timestamps_last(root: Path, client) -> None:
    write_json(root / "jobs" / "pending" / "no-timestamp.json", {"job_id": "no-timestamp"})
    write_json(
        root / "jobs" / "pending" / "dated.json",
        {"job_id": "dated", "created_at": datetime.now(timezone.utc).astimezone().isoformat()},
    )

    response = client.get("/api/queue/pending")

    assert [job["job_id"] for job in response.get_json()["jobs"]] == ["dated", "no-timestamp"]


def test_queue_pending_empty_reports_empty_list(client) -> None:
    response = client.get("/api/queue/pending")

    assert response.status_code == 200
    assert response.get_json() == {"jobs": [], "total": 0, "warn_after_sec": admin_panel.PENDING_DWELL_WARN_SEC}


def test_queue_pending_truncates_to_limit_but_reports_true_total(root: Path, client) -> None:
    for index in range(5):
        write_json(
            root / "jobs" / "pending" / f"job{index}.json",
            {"job_id": f"job{index}", "created_at": datetime.now(timezone.utc).astimezone().isoformat()},
        )

    with patch("admin_panel.PENDING_LIST_LIMIT", 2):
        response = client.get("/api/queue/pending")

    data = response.get_json()
    assert data["total"] == 5
    assert len(data["jobs"]) == 2


def test_random_range_sweep_seed_reproduces_preview_and_submitted_params(root: Path, client) -> None:
    first = client.post("/api/sweep/preview", json=sweep_payload(seed=None))
    assert first.status_code == 200
    first_data = first.get_json()
    seed = first_data["seed"]
    assert isinstance(seed, int)

    replay = client.post("/api/sweep/preview", json=sweep_payload(seed=seed))
    assert replay.status_code == 200
    expected = [job["params"] for job in replay.get_json()["jobs"]]
    assert [job["params"] for job in first_data["jobs"]] == expected

    submitted = client.post("/api/sweep/submit", json=sweep_payload(seed=seed))
    assert submitted.status_code == 200
    actual = [
        json.loads(path.read_text(encoding="utf-8"))["params"]
        for path in sorted((root / "jobs" / "pending").glob("*.json"))
    ]
    assert actual == expected


def test_range_sweep_rejects_prefix_with_path_separator(client) -> None:
    response = client.post("/api/sweep/preview", json=sweep_payload(prefix="../evil"))

    assert response.status_code == 400
    assert "prefix" in response.get_json()["error"]


def test_range_sweep_rejects_low_greater_than_or_equal_to_high(client) -> None:
    response = client.post(
        "/api/sweep/preview",
        json=sweep_payload(params={"alpha": {"type": "float", "low": 1, "high": 1}}),
    )

    assert response.status_code == 400
    assert "low" in response.get_json()["error"]


def test_upload_zip_preview_accepts_one_job_and_reports_three_skips(client) -> None:
    valid = json.dumps({"job_id": "job001", "command": ["python", "solver.py"]}).encode()
    contents = make_zip(
        [
            ("good.json", valid),
            ("duplicate.json", json.dumps({"job_id": "job001", "command": "other"}).encode()),
            ("bad.json", b"{not json"),
            ("readme.txt", b"not a job"),
        ]
    )

    response = client.post(
        "/api/jobs/upload-zip/preview",
        data={"file": (contents, "jobs.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    data = response.get_json()
    assert [job["job_id"] for job in data["accepted"]] == ["job001"]
    assert len(data["skipped"]) == 3
    assert {entry["filename"] for entry in data["skipped"]} == {"duplicate.json", "bad.json", "readme.txt"}
    assert any("重複" in entry["reason"] for entry in data["skipped"])


def test_upload_zip_rejects_job_id_containing_path_separator(root: Path, client) -> None:
    traversal = json.dumps({"job_id": "../../escape", "command": "solver"}).encode()
    contents = make_zip([("evil.json", traversal)])

    response = client.post(
        "/api/jobs/upload-zip/preview",
        data={"file": (contents, "jobs.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["accepted"] == []
    assert data["skipped"] == [{"filename": "evil.json", "reason": "job_id に使用できない文字が含まれています(英数字・._- のみ)"}]
    assert not (root.parent / "escape.json").exists()


def test_upload_zip_allows_valid_duplicate_after_invalid_command(client) -> None:
    contents = make_zip(
        [
            ("invalid-first.json", json.dumps({"job_id": "job001"}).encode()),
            ("valid-second.json", json.dumps({"job_id": "job001", "command": "solver"}).encode()),
        ]
    )

    response = client.post(
        "/api/jobs/upload-zip/preview",
        data={"file": (contents, "jobs.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    data = response.get_json()
    assert [job["filename"] for job in data["accepted"]] == ["valid-second.json"]
    assert data["skipped"] == [{"filename": "invalid-first.json", "reason": "command が不正です"}]


@pytest.mark.parametrize(
    "payload",
    [
        search_payload(params={}),
        search_payload(instances=[]),
        search_payload(name="invalid name"),
    ],
)
def test_search_launch_rejects_invalid_inputs(client, payload: dict) -> None:
    response = client.post("/api/search/launch", json=payload)
    assert response.status_code == 400


def test_search_launch_mocks_process_and_passes_bridge_options(root: Path, client) -> None:
    process = MagicMock()
    process.poll.return_value = None
    with patch("admin_panel.subprocess.Popen", return_value=process) as popen:
        response = client.post("/api/search/launch", json=search_payload())

    assert response.status_code == 200
    assert response.get_json() == {"name": "trial", "started": True, "spec_path": "state/panel_specs/trial.json"}
    command = popen.call_args.args[0]
    assert command[command.index("--max-trials") + 1] == "12"
    assert command[command.index("--parallel") + 1] == "3"
    assert command[command.index("--agg") + 1] == "mean"
    assert command[command.index("--direction") + 1] == "max"
    assert popen.call_args.kwargs["cwd"] == str(root)


def test_search_launch_blocks_a_second_live_process(client) -> None:
    process = MagicMock()
    process.poll.return_value = None
    with patch("admin_panel.subprocess.Popen", return_value=process) as popen:
        first = client.post("/api/search/launch", json=search_payload())
        second = client.post("/api/search/launch", json=search_payload())

    assert first.status_code == 200
    assert second.status_code == 400
    assert "既に実行中" in second.get_json()["error"]
    popen.assert_called_once()


def test_templates_can_be_saved_listed_and_deleted_and_reject_bad_kind(client) -> None:
    bad_kind = client.post("/api/templates/save", json={"kind": "other", "name": "base", "payload": {}})
    saved = client.post("/api/templates/save", json={"kind": "sweep", "name": "base", "payload": {"count": 5}})
    listed = client.get("/api/templates/list")
    deleted = client.post("/api/templates/delete", json={"kind": "sweep", "name": "base"})
    listed_after_delete = client.get("/api/templates/list")

    assert bad_kind.status_code == 400
    assert saved.status_code == 200
    assert listed.get_json()["templates"][0]["payload"] == {"count": 5}
    assert deleted.status_code == 200
    assert listed_after_delete.get_json()["templates"] == []


def test_optuna_dashboard_launch_passes_plain_journal_path(root: Path, client) -> None:
    journal_path = root / "state" / "search" / "trial.journal.log"
    write_json(journal_path, {})  # only existence matters; content is never parsed here

    process = MagicMock()
    process.poll.return_value = None
    with (
        patch("admin_panel.subprocess.Popen", return_value=process) as popen,
        patch("admin_panel.shutil.which", return_value="/usr/bin/optuna-dashboard"),
        patch("admin_panel.wait_for_port", return_value=True),
    ):
        response = client.post("/api/optuna-dashboard/launch", json={"name": "trial"})

    assert response.status_code == 200
    data = response.get_json()
    assert data["reused"] is False
    assert data["url"] == "http://127.0.0.1:" + str(data["port"]) + "/"
    command = popen.call_args.args[0]
    # optuna-dashboard の CLI は生のパスをそのまま受け取る(journal: のようなURLスキームは存在しない)。
    assert command[0] == "optuna-dashboard"
    assert command[1] == str(journal_path)
    assert not command[1].startswith("journal:")


def test_optuna_dashboard_launch_missing_journal_returns_error(client) -> None:
    response = client.post("/api/optuna-dashboard/launch", json={"name": "no-such-search"})

    assert response.status_code == 400
    assert "journal" in response.get_json()["error"]


def test_optuna_dashboard_launch_reports_error_when_process_never_becomes_reachable(root: Path, client) -> None:
    write_json(root / "state" / "search" / "trial.journal.log", {})

    process = MagicMock()
    process.poll.return_value = None
    with (
        patch("admin_panel.subprocess.Popen", return_value=process),
        patch("admin_panel.shutil.which", return_value="/usr/bin/optuna-dashboard"),
        patch("admin_panel.wait_for_port", return_value=False),
    ):
        response = client.post("/api/optuna-dashboard/launch", json={"name": "trial"})

    assert response.status_code == 500
    assert "optuna-dashboard" in response.get_json()["error"]


def test_wait_for_port_returns_true_once_a_real_listener_accepts_connections() -> None:
    import socket as socket_module
    from unittest.mock import MagicMock as Mock

    server = socket_module.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        never_exited = Mock()
        never_exited.poll.return_value = None
        assert admin_panel.wait_for_port(port, never_exited, timeout=2.0) is True
    finally:
        server.close()


def test_wait_for_port_returns_false_when_process_exits_before_listening() -> None:
    from unittest.mock import MagicMock as Mock

    with admin_panel.socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]

    exited = Mock()
    exited.poll.return_value = 1
    assert admin_panel.wait_for_port(closed_port, exited, timeout=2.0) is False


def test_archive_parses_archive_script_summary(client) -> None:
    completed = SimpleNamespace(
        returncode=0,
        stdout="archived to /fake/path: done=3 failed=1 result-dirs=4\n",
        stderr="",
    )
    with patch("admin_panel.subprocess.run", return_value=completed):
        response = client.post("/api/archive", json={"label": "release"})

    assert response.status_code == 200
    assert response.get_json() == {"archived_to": "/fake/path", "done": 3, "failed": 1, "result_dirs": 4}

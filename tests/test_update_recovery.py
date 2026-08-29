import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]

MOCK_DOCKER = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys


state_path = Path(os.environ["MOCK_DOCKER_STATE"])


def load():
    return json.loads(state_path.read_text())


def save(state):
    state_path.write_text(json.dumps(state))


args = sys.argv[1:]
state = load()

if args == ["info"]:
    raise SystemExit(0)

if args[:2] == ["container", "inspect"]:
    container = state.get("container")
    if container is None or state.get("inspect_error"):
        raise SystemExit(1)
    print(
        "|".join(
            [
                container["id"],
                container["image"],
                container["status"],
                container["health"],
                str(container.get("restarts", 0)),
                container.get("started_at", "2026-08-29T00:00:00Z"),
            ]
        )
    )
    raise SystemExit(0)

if args[:3] == ["container", "ls", "--all"]:
    if state.get("list_error"):
        raise SystemExit(1)
    container = state.get("container")
    if container is not None:
        print(container["id"])
    raise SystemExit(0)

if args[:3] == ["container", "rm", "--force"]:
    state["container"] = None
    save(state)
    raise SystemExit(0)

if args[:2] == ["image", "inspect"]:
    image_ref = args[-1]
    if "org.opencontainers.image.version" in " ".join(args):
        version = state.get("versions", {}).get(image_ref)
        if version is None:
            raise SystemExit(1)
        print(version)
        raise SystemExit(0)
    image_id = state.get("images", {}).get(image_ref)
    if image_id is None:
        raise SystemExit(1)
    print(image_id)
    raise SystemExit(0)

if args and args[0] == "exec":
    container = state.get("container")
    if container is None or not container.get("version"):
        raise SystemExit(1)
    print(container["version"])
    raise SystemExit(0)

if args and args[0] == "logs":
    print("Logged in as test-bot")
    raise SystemExit(0)

if args and args[0] == "compose":
    if state.get("compose_error"):
        raise SystemExit(1)
    image_ref = os.environ.get("BOT_IMAGE")
    image_id = state.get("images", {}).get(image_ref)
    version = state.get("versions", {}).get(image_ref)
    if image_id is None or version is None:
        raise SystemExit(1)
    state["compose_runs"] = state.get("compose_runs", 0) + 1
    state["container"] = {
        "id": f"rollback-{state['compose_runs']}",
        "image": image_id,
        "status": "running",
        "health": "healthy",
        "restarts": 0,
        "started_at": "2026-08-29T00:01:00Z",
        "version": version,
    }
    save(state)
    raise SystemExit(0)

print(f"unexpected docker invocation: {args!r}", file=sys.stderr)
raise SystemExit(64)
"""


def _container(container_id, image_id, version, status="running", health="healthy"):
    return {
        "id": container_id,
        "image": image_id,
        "status": status,
        "health": health,
        "restarts": 0,
        "started_at": "2026-08-29T00:00:00Z",
        "version": version,
    }


def _run_recovery(tmp_path, journal, state, env_overrides=None, reject_as_directory=False):
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    docker = mock_bin / "docker"
    docker.write_text(MOCK_DOCKER)
    docker.chmod(0o755)

    for command in ("git", "sleep"):
        executable = mock_bin / command
        exit_code = 1 if command == "git" else 0
        executable.write_text(f"#!/bin/sh\nexit {exit_code}\n")
        executable.chmod(0o755)

    state_path = tmp_path / "docker-state.json"
    state_path.write_text(json.dumps(state))
    home = tmp_path / "home"
    pending = home / "Library/Application Support/musicbot/deploy-pending.tsv"
    pending.parent.mkdir(parents=True)
    pending.write_text(journal + "\n")
    if reject_as_directory:
        (pending.parent / "rejected-digests.txt").mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "MUSICBOT_UPDATE_LOCKED": "1",
            "MOCK_DOCKER_STATE": str(state_path),
            "PATH": f"{mock_bin}:{env['PATH']}",
        }
    )
    env.update(env_overrides or {})
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "bin/update.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return result, pending, state_path, home


def test_completed_rollback_is_recovered(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    rollback = "music-bot-rollback:pre-old"
    journal = "\t".join(
        [
            "v2",
            rollback,
            "sha256:old",
            "original-container",
            "2026.8.27",
            candidate,
            "2026.8.28",
        ]
    )
    state = {
        "container": _container("recreated-old", "sha256:old", "2026.8.27"),
        "images": {candidate: "sha256:new", rollback: "sha256:old"},
        "versions": {rollback: "2026.8.27"},
    }

    result, pending, _, _ = _run_recovery(tmp_path, journal, state)

    assert result.returncode == 1  # Recovery succeeded; the mocked git fetch then stops.
    assert not pending.exists()
    assert "Recovering an interrupted rollback" in result.stdout


def test_rejected_candidate_is_never_reaccepted(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    rollback = "music-bot-rollback:pre-old"
    journal = "\t".join(
        [
            "v3",
            "rollback-rejected",
            rollback,
            "sha256:old",
            "original-container",
            "2026.8.27",
            candidate,
            "2026.8.28",
        ]
    )
    state = {
        "container": _container("candidate", "sha256:new", "2026.8.28"),
        "images": {candidate: "sha256:new", rollback: "sha256:old"},
        "versions": {rollback: "2026.8.27"},
    }

    result, pending, state_path, home = _run_recovery(tmp_path, journal, state)

    recovered = json.loads(state_path.read_text())
    rejected = home / "Library/Application Support/musicbot/rejected-digests.txt"
    assert result.returncode == 1
    assert not pending.exists()
    assert rejected.read_text().splitlines() == [candidate]
    assert recovered["container"]["image"] == "sha256:old"
    assert recovered["compose_runs"] == 1


def test_activating_candidate_failure_is_promoted_to_durable_rejection(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    rollback = "music-bot-rollback:pre-old"
    journal = "\t".join(
        [
            "v3",
            "activating",
            rollback,
            "sha256:old",
            "original-container",
            "2026.8.27",
            candidate,
            "2026.8.28",
        ]
    )
    state = {
        "container": _container(
            "candidate", "sha256:new", "2026.8.28", status="exited", health="unhealthy"
        ),
        "images": {candidate: "sha256:new", rollback: "sha256:old"},
        "versions": {rollback: "2026.8.27"},
        "compose_error": True,
    }

    result, pending, state_path, home = _run_recovery(tmp_path, journal, state)

    recovered = json.loads(state_path.read_text())
    rejected = home / "Library/Application Support/musicbot/rejected-digests.txt"
    assert result.returncode == 1
    assert pending.read_text().split("\t", 2)[:2] == ["v3", "rollback-rejected"]
    assert rejected.read_text().splitlines() == [candidate]
    assert recovered["container"]["image"] == "sha256:new"


def test_rejection_storage_failure_preserves_journal_after_rollback(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    rollback = "music-bot-rollback:pre-old"
    journal = "\t".join(
        [
            "v3",
            "rollback-rejected",
            rollback,
            "sha256:old",
            "original-container",
            "2026.8.27",
            candidate,
            "2026.8.28",
        ]
    )
    state = {
        "container": _container("candidate", "sha256:new", "2026.8.28"),
        "images": {candidate: "sha256:new", rollback: "sha256:old"},
        "versions": {rollback: "2026.8.27"},
    }

    result, pending, state_path, _ = _run_recovery(
        tmp_path,
        journal,
        state,
        reject_as_directory=True,
    )

    recovered = json.loads(state_path.read_text())
    assert result.returncode == 1
    assert pending.exists()
    assert recovered["container"]["image"] == "sha256:old"
    assert "refusing to clear the journal" in result.stdout


def test_failed_initial_candidate_is_removed(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    journal = "\t".join(
        [
            "v3",
            "rollback-rejected",
            "none",
            "none",
            "none",
            "-",
            candidate,
            "2026.8.28",
        ]
    )
    state = {
        "container": _container(
            "candidate", "sha256:new", "2026.8.28", status="exited", health="unhealthy"
        ),
        "images": {candidate: "sha256:new"},
        "versions": {},
    }

    result, pending, state_path, home = _run_recovery(tmp_path, journal, state)

    rejected = home / "Library/Application Support/musicbot/rejected-digests.txt"
    assert result.returncode == 1
    assert not pending.exists()
    assert json.loads(state_path.read_text())["container"] is None
    assert rejected.read_text().splitlines() == [candidate]


def test_legacy_initial_preactivation_journal_is_cleared(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    journal = "\t".join(["none", "none", "-", candidate, "2026.8.28"])
    state = {
        "container": None,
        "images": {candidate: "sha256:new"},
        "versions": {},
    }

    result, pending, _, _ = _run_recovery(tmp_path, journal, state)

    assert result.returncode == 1
    assert not pending.exists()
    assert "legacy v1" in result.stdout


def test_docker_api_error_is_not_treated_as_container_absence(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    journal = "\t".join(["none", "none", "-", candidate, "2026.8.28"])
    state = {
        "container": None,
        "images": {candidate: "sha256:new"},
        "versions": {},
        "inspect_error": True,
        "list_error": True,
    }

    result, pending, _, _ = _run_recovery(tmp_path, journal, state)

    assert result.returncode == 1
    assert pending.exists()
    assert "reliable container snapshot" in result.stdout


def test_invalid_ready_timeout_is_rejected_before_recovery(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    journal = "\t".join(["none", "none", "-", candidate, "2026.8.28"])
    state = {
        "container": None,
        "images": {candidate: "sha256:new"},
        "versions": {},
    }

    result, pending, _, _ = _run_recovery(
        tmp_path,
        journal,
        state,
        {"MUSICBOT_READY_TIMEOUT_SECONDS": "not-a-number"},
    )

    assert result.returncode == 2
    assert pending.exists()
    assert "must be an integer" in result.stderr


def test_leading_zero_ready_timeout_is_normalized_as_decimal(tmp_path):
    candidate = "ghcr.io/example/bot@sha256:new"
    rollback = "music-bot-rollback:pre-old"
    journal = "\t".join(
        [
            "v3",
            "activating",
            rollback,
            "sha256:old",
            "original-container",
            "2026.8.27",
            candidate,
            "2026.8.28",
        ]
    )
    state = {
        "container": _container("candidate", "sha256:new", "2026.8.28"),
        "images": {candidate: "sha256:new", rollback: "sha256:old"},
        "versions": {rollback: "2026.8.27"},
    }

    result, pending, _, _ = _run_recovery(
        tmp_path,
        journal,
        state,
        {"MUSICBOT_READY_TIMEOUT_SECONDS": "08"},
    )

    assert result.returncode == 1
    assert not pending.exists()

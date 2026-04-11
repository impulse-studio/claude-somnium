"""Stop hook: trigger the dream mode pipeline after a session ends.

The Stop hook runs synchronously (Claude Code blocks on it briefly).
Since the full dream pipeline can take 30s-5min, we do this:

  1. Read the hook event JSON from stdin.
  2. Run the heuristic gate synchronously (fast, <10ms).
  3. If the gate says SKIP, write a short "skipped" digest and exit 0.
  4. If the gate says RUN, spawn the dream runner in a **detached**
     background subprocess and exit 0 immediately.

The detached subprocess then calls `somnium-dream-run` (another console
script) which executes agent + router + digest. This way Claude Code
is never blocked by the LLM call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config import find_project_root, load_config
from ..dream.agent import DREAM_SUBAGENT_ENV_VAR
from ..dream.digest import write_digest
from ..dream.gate import GateDecision, decide
from ..dream.transcript import load_transcript
from ._common import log_error, log_info, read_event

HOOK_NAME = "stop"


def _resolve_dream_runner_path() -> str:
    """Find the absolute path to the somnium-dream-run binary.

    Claude Code launches hooks from a clean shell that may not include
    our venv on PATH, so we always use an absolute path.
    """
    import shutil
    import sys

    resolved = shutil.which("somnium-dream-run")
    if resolved:
        return resolved
    candidate = Path(sys.executable).parent / "somnium-dream-run"
    if candidate.exists():
        return str(candidate)
    return "somnium-dream-run"


def _spawn_detached_runner(transcript_path: str, cwd: str | None) -> None:
    """Fire-and-forget the dream runner. The parent exits immediately."""
    env = os.environ.copy()
    runner_path = _resolve_dream_runner_path()
    cmd = [runner_path, "--transcript", transcript_path]
    if cwd:
        cmd.extend(["--cwd", cwd])

    log_dir = Path.home() / ".claude" / "somnium" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # We deliberately open without a `with` block because Popen needs
    # the file descriptors to survive past the handle close here — the
    # child inherits them on `start_new_session=True`. Closing our copy
    # in the finally is enough; the kernel keeps the fd alive for the
    # child. Ruff SIM115 would suggest a context manager, which breaks
    # the inheritance.
    stdout_log = (log_dir / "dream-run.log").open("ab", buffering=0)
    stderr_log = (log_dir / "dream-run.err").open("ab", buffering=0)

    try:
        subprocess.Popen(
            cmd,
            stdout=stdout_log,
            stderr=stderr_log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach
            env=env,
        )
    finally:
        stdout_log.close()
        stderr_log.close()


def handle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Core stop-hook logic."""
    # Recursion guard: if we're running inside a dream sub-agent
    # subprocess, do not trigger another dream.
    if os.environ.get(DREAM_SUBAGENT_ENV_VAR):
        return {"skipped": "dream sub-agent context"}

    transcript_path_str = event.get("transcript_path") or event.get("transcriptPath")
    cwd = event.get("cwd")

    if not transcript_path_str:
        return {"skipped": "no transcript_path in event"}

    transcript_path = Path(transcript_path_str)
    if not transcript_path.exists():
        return {"skipped": "transcript file does not exist"}

    # Resolve config with the session's cwd so project root is picked up.
    project_root = find_project_root(Path(cwd)) if cwd else None
    config = load_config(project_root=project_root)

    if not config.dream.enabled:
        return {"skipped": "dream.enabled=false"}

    transcript = load_transcript(transcript_path)
    gate_result = decide(transcript, config)

    if gate_result.decision == GateDecision.SKIP:
        # Still write a tiny digest so the session is traceable.
        try:
            write_digest(
                config=config,
                transcript=transcript,
                gate=gate_result,
                dream=None,
                records=None,
            )
        except Exception as exc:
            log_error(HOOK_NAME, exc)
        return {"gate": "skip", "reason": gate_result.reason}

    # RUN: spawn a detached runner so we return fast.
    _spawn_detached_runner(str(transcript_path), cwd)
    return {
        "gate": "run",
        "reason": gate_result.reason,
        "dispatched": True,
    }


def main() -> None:
    event = read_event()
    try:
        result = handle_event(event)
        log_info(HOOK_NAME, str(result))
    except BaseException as exc:
        log_error(HOOK_NAME, exc)
    sys.exit(0)


if __name__ == "__main__":
    main()

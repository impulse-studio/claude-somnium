"""Detached dream runner entry point.

Invoked as `somnium-dream-run --transcript <path> [--cwd <dir>]` by
the Stop hook. Runs the full dream pipeline and exits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import find_project_root, load_config
from ..hooks._common import log_error, log_info
from .runner import run_dream


def main() -> None:
    parser = argparse.ArgumentParser(prog="somnium-dream-run")
    parser.add_argument("--transcript", required=True, help="Path to the transcript JSONL.")
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory of the session, used to find project root.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the gate and always run the dream agent.",
    )
    args = parser.parse_args()

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"transcript not found: {transcript_path}", file=sys.stderr)
        sys.exit(2)

    project_root = find_project_root(Path(args.cwd)) if args.cwd else None
    config = load_config(project_root=project_root)

    try:
        result = run_dream(
            transcript_path=transcript_path,
            config=config,
            force=args.force,
        )
    except Exception as exc:
        log_error("dream_runner", exc)
        sys.exit(1)

    log_info(
        "dream_runner",
        f"decision={result.gate_result.decision.value} "
        f"agent_ran={result.ran_agent} "
        f"items_written={len(result.write_records)} "
        f"digest={result.digest_path}",
    )
    if result.error:
        print(f"dream error: {result.error}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

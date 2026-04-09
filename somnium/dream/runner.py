"""Dream runner: orchestrates gate → agent → router → digest.

Called from two places:
  - The Stop hook, with the transcript_path provided by Claude Code.
  - The `somnium dream` CLI command, for manual triggering on the
    most recent session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import SomniumConfig
from . import gate as gate_module
from .agent import DreamAgentError, DreamResult, run_dream_agent
from .digest import write_digest
from .gate import GateDecision, GateResult
from .router import WriteRecord, dispatch
from .transcript import Transcript, load_transcript


@dataclass
class DreamRunResult:
    gate_result: GateResult
    transcript_path: Path | None
    digest_path: Path | None = None
    dream_result: DreamResult | None = None
    write_records: list[WriteRecord] = field(default_factory=list)
    error: str | None = None

    @property
    def ran_agent(self) -> bool:
        return self.dream_result is not None


def run_dream(
    *,
    transcript_path: Path,
    config: SomniumConfig,
    force: bool = False,
) -> DreamRunResult:
    """Execute the full dream pipeline for a given transcript file.

    If `force` is True, the gate is bypassed (used by the manual
    `/dream` or `somnium dream` command).
    """
    transcript = load_transcript(transcript_path)

    # Gate
    if force:
        gate_result = GateResult(
            decision=GateDecision.RUN,
            reason="forced via manual trigger",
            category="manual",
        )
    else:
        gate_result = gate_module.decide(transcript, config)

    result = DreamRunResult(
        gate_result=gate_result,
        transcript_path=transcript_path,
    )

    if gate_result.decision == GateDecision.SKIP:
        result.digest_path = write_digest(
            config=config,
            transcript=transcript,
            gate=gate_result,
            dream=None,
            records=None,
        )
        return result

    # Run the dream agent
    try:
        dream = run_dream_agent(transcript, config)
        result.dream_result = dream
    except DreamAgentError as exc:
        result.error = str(exc)
        result.digest_path = write_digest(
            config=config,
            transcript=transcript,
            gate=gate_result,
            dream=None,
            records=None,
            error=str(exc),
        )
        return result

    # Dispatch items if worth persisting
    if dream.should_persist and dream.items:
        records = dispatch(dream.items, config)
        result.write_records = records

    result.digest_path = write_digest(
        config=config,
        transcript=transcript,
        gate=gate_result,
        dream=dream,
        records=result.write_records,
    )
    return result

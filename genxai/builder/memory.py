"""Generation memory: an episodic record of past generations for recall.

Every generation is recorded (prompt + plan); when the user accepts a draft
(e.g. saves it in the Studio) the record is marked accepted. On new requests,
the most similar past plans — accepted ones first — are injected into the
planner prompt as grounded examples, so the planner learns the shapes and
capability choices this deployment's users actually keep.

Storage is a JSONL file: append on record, rewrite on acceptance. Similarity
is token overlap — cheap, deterministic, and good enough for few-shot recall.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from genxai.builder.schemas import WorkflowPlan

logger = logging.getLogger(__name__)

_ACCEPTED_BONUS = 0.5


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _overlap(query: set[str], candidate: set[str]) -> float:
    if not query or not candidate:
        return 0.0
    return len(query & candidate) / len(query)


class GenerationRecord(BaseModel):
    """One remembered generation."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    prompt: str
    plan: dict = Field(..., description="WorkflowPlan dump")
    accepted: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class GenerationMemory:
    """JSONL-backed episodic memory of generated plans."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[GenerationRecord]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(GenerationRecord.model_validate_json(line))
            except ValueError:
                logger.warning("Skipping corrupt generation-memory line")
        return records

    def _save(self, records: list[GenerationRecord]) -> None:
        self.path.write_text("".join(record.model_dump_json() + "\n" for record in records))

    def record(self, prompt: str, plan: WorkflowPlan) -> str:
        """Remember a generation; returns the record id."""
        record = GenerationRecord(prompt=prompt, plan=plan.model_dump(mode="json"))
        with self.path.open("a") as handle:
            handle.write(record.model_dump_json() + "\n")
        return record.id

    def mark_accepted(self, record_id: str) -> bool:
        """Mark a remembered generation as accepted (user kept the draft)."""
        records = self._load()
        for record in records:
            if record.id == record_id:
                record.accepted = True
                self._save(records)
                return True
        return False

    def recall(self, prompt: str, limit: int = 2) -> list[GenerationRecord]:
        """Most similar past records, accepted ones weighted up."""
        query = _tokens(prompt)
        scored = [
            (
                _overlap(query, _tokens(record.prompt))
                + (_ACCEPTED_BONUS if record.accepted else 0.0),
                record,
            )
            for record in self._load()
        ]
        scored = [(score, record) for score, record in scored if score > 0.0]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _score, record in scored[:limit]]

    def render_for_prompt(self, records: list[GenerationRecord]) -> str:
        """Render recalled records as planner prompt context."""
        if not records:
            return ""
        blocks = []
        for record in records:
            status = "accepted by the user" if record.accepted else "generated previously"
            plan_json = json.dumps(record.plan, indent=2, default=str)
            blocks.append(
                f"### Past request ({status})\n{record.prompt}\n" f"### Its plan\n{plan_json}"
            )
        return (
            "Plans produced for similar past requests (favor the shapes and "
            "capability choices of accepted ones):\n\n" + "\n\n".join(blocks)
        )

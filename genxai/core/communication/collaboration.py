"""Collaboration protocol implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class VotingResult:
    winner: Any
    counts: dict[Any, int]


class VotingProtocol:
    """Simple majority voting protocol."""

    async def run(self, inputs: list[Any], metadata: dict[str, Any]) -> VotingResult:
        counts: dict[Any, int] = {}
        for value in inputs:
            counts[value] = counts.get(value, 0) + 1
        winner = max(counts, key=counts.get)
        return VotingResult(winner=winner, counts=counts)


class NegotiationProtocol:
    """Simple negotiation protocol that returns consensus if all equal."""

    async def run(self, inputs: list[Any], metadata: dict[str, Any]) -> Any:
        if not inputs:
            return None
        first = inputs[0]
        if all(value == first for value in inputs):
            return first
        return metadata.get("fallback")


class AuctionProtocol:
    """Simple auction protocol selecting max bid from inputs."""

    async def run(self, inputs: list[Any], metadata: dict[str, Any]) -> Any:
        if not inputs:
            return None
        return max(inputs)

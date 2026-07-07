"""Flow orchestrators for common agent coordination patterns."""

from genxai.flows.auction import AuctionFlow
from genxai.flows.base import FlowOrchestrator
from genxai.flows.conditional import ConditionalFlow
from genxai.flows.coordinator_worker import CoordinatorWorkerFlow
from genxai.flows.critic_review import CriticReviewFlow
from genxai.flows.ensemble_voting import EnsembleVotingFlow
from genxai.flows.loop import LoopFlow
from genxai.flows.map_reduce import MapReduceFlow
from genxai.flows.p2p import P2PFlow
from genxai.flows.parallel import ParallelFlow
from genxai.flows.round_robin import RoundRobinFlow
from genxai.flows.router import RouterFlow
from genxai.flows.selector import SelectorFlow
from genxai.flows.subworkflow import SubworkflowFlow

# Flow patterns addressable by name from workflow definitions (FlowNode).
# Conditional/Router/Selector/Subworkflow are excluded: their constructors
# require Python callables or a pre-built Graph, which a serialized workflow
# document cannot express.
FLOW_TYPES: dict[str, type[FlowOrchestrator]] = {
    "round_robin": RoundRobinFlow,
    "parallel": ParallelFlow,
    "auction": AuctionFlow,
    "coordinator_worker": CoordinatorWorkerFlow,
    "critic_review": CriticReviewFlow,
    "ensemble_voting": EnsembleVotingFlow,
    "map_reduce": MapReduceFlow,
    "p2p": P2PFlow,
}

__all__ = [
    "FLOW_TYPES",
    "FlowOrchestrator",
    "RoundRobinFlow",
    "SelectorFlow",
    "P2PFlow",
    "ParallelFlow",
    "ConditionalFlow",
    "LoopFlow",
    "RouterFlow",
    "EnsembleVotingFlow",
    "CriticReviewFlow",
    "CoordinatorWorkerFlow",
    "MapReduceFlow",
    "SubworkflowFlow",
    "AuctionFlow",
]

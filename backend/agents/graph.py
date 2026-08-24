from typing import TypedDict, List, Dict
from langgraph.graph import StateGraph, END
from .nodes import (
    extraction_node,
    validation_node,
    duplicate_check_node,
    matching_node,
    decision_node,
)


class AgentState(TypedDict):
    raw_text: str
    extracted_data: Dict
    exceptions: List[Dict]
    status: str
    reasoning: List[str]
    invoice_id: int          # DB primary key — set by main.py before invocation


def _route_after_validation(state: AgentState) -> str:
    """
    Conditional edge after validation_node.
    If extraction failed, skip duplicate check + matching and go straight to
    decision_node.  Otherwise continue to duplicate_check_node.
    """
    if state.get("status") == "EXTRACTION_FAILED":
        return "decide"   # decision_node will emit EXCEPTION and terminate
    return "duplicate_check"


workflow = StateGraph(AgentState)

workflow.add_node("extract", extraction_node)
workflow.add_node("validate", validation_node)
workflow.add_node("duplicate_check", duplicate_check_node)
workflow.add_node("match", matching_node)
workflow.add_node("decide", decision_node)

workflow.set_entry_point("extract")
workflow.add_edge("extract", "validate")

# Conditional branch: failed extraction bypasses duplicate check + matching
workflow.add_conditional_edges(
    "validate",
    _route_after_validation,
    {
        "duplicate_check": "duplicate_check",
        "decide": "decide",
    },
)

# Duplicate check always feeds into PO/contract matching so both types of
# exceptions accumulate before the final decision.
workflow.add_edge("duplicate_check", "match")
workflow.add_edge("match", "decide")
workflow.add_edge("decide", END)

app_agent = workflow.compile()

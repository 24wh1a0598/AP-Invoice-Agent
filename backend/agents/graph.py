from typing import TypedDict, List, Dict
from langgraph.graph import StateGraph, END
from .nodes import extraction_node, validation_node, matching_node, decision_node


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
    If extraction failed, skip matching and go straight to END.
    Otherwise continue to the matching node.
    """
    if state.get("status") == "EXTRACTION_FAILED":
        return "decide"   # decision_node will emit EXCEPTION and terminate
    return "match"


workflow = StateGraph(AgentState)

workflow.add_node("extract", extraction_node)
workflow.add_node("validate", validation_node)
workflow.add_node("match", matching_node)
workflow.add_node("decide", decision_node)

workflow.set_entry_point("extract")
workflow.add_edge("extract", "validate")

# Conditional branch: failed extraction bypasses matching
workflow.add_conditional_edges(
    "validate",
    _route_after_validation,
    {
        "match": "match",
        "decide": "decide",
    },
)

workflow.add_edge("match", "decide")
workflow.add_edge("decide", END)

app_agent = workflow.compile()

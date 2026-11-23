from typing import List, Literal

from langchain_core.documents import Document
from pydantic import BaseModel, Field
from typing_extensions import NotRequired, TypedDict


class RouteDecision(BaseModel):
    route: Literal["kb", "direct", "out_of_scope"] = Field(
        description=(
            "kb for HR/workplace questions, direct for greetings/simple chat, "
            "out_of_scope for non-HR requests"
        )
    )


class EvidenceGrade(BaseModel):
    grade: Literal["good", "weak"] = Field(
        description="Whether evidence is sufficient to answer"
    )


class ConversationMemorySummary(BaseModel):
    summary: str = Field(
        description=(
            "A concise, useful summary of this conversation for future turns. "
            "Keep explicit user context, preferences, decisions, and unresolved "
            "follow-ups; omit sensitive personal data and unsupported inferences."
        )
    )


class AgentState(TypedDict):
    user_id: NotRequired[str]
    route: NotRequired[Literal["kb", "direct", "out_of_scope"]]
    question: str
    current_query: str
    kb_docs: List[Document]
    web_results: str
    kb_grade: str
    web_grade: str
    answer: str
    source_used: str
    retry_count: int
    trace: List[str]
    citations: List[dict]
    conversation_context: NotRequired[str]

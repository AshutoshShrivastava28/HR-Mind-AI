import logging
from typing import Literal

from langchain_groq import ChatGroq
from langchain_tavily import TavilySearch
from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.rag.state import AgentState, EvidenceGrade, RouteDecision
from app.rag.vectorstore import get_retriever

logger = logging.getLogger(__name__)
settings = get_settings()

_llm = None
_web_search = None


def llm():
    global _llm

    if _llm is None:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is missing")

        _llm = ChatGroq(
            model="openai/gpt-oss-120b",
            temperature=0,
            api_key=settings.groq_api_key,
        )

    return _llm


def web_search_tool():
    global _web_search

    if _web_search is None:
        if not settings.tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY is missing")

        _web_search = TavilySearch(
            tavily_api_key=settings.tavily_api_key,
            max_results=5,
            topic="general",
            include_answer=True,
            include_raw_content=False,
        )

    return _web_search


def add_trace(state: AgentState, message: str):
    return [*state.get("trace", []), message]


def route_question(state: AgentState):
    router = llm().with_structured_output(
        RouteDecision,
        method="json_mode",
    )

    decision = router.invoke(f"""
You route messages for an enterprise HR policy and employee support assistant.
Use kb for questions about company HR policies, leave, holidays, benefits, payroll,
remote work, attendance, onboarding, performance, expenses, travel, conduct, or employee support.
Use direct only for greetings, thanks, or casual chat that needs no company knowledge.
Question: {state['question']}
Return valid JSON like {{"route":"kb"}}.
""")

    return {
        "source_used": decision.route,
        "trace": add_trace(
            state,
            f"Router → {decision.route.upper()}",
        ),
    }


def route_after_router(
    state: AgentState,
) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


def retrieve_kb(state: AgentState):
    # Shared company documents are available to all authenticated users.
    docs = get_retriever(settings.pinecone_namespace).invoke(state["current_query"])

    # Only retrieve private documents from the current user's namespace.
    user_id = state.get("user_id")

    if user_id:
        private_namespace = f"user-{user_id}"

        personal_docs = get_retriever(private_namespace).invoke(state["current_query"])

        docs = list(docs) + list(personal_docs)

    return {
        "kb_docs": docs,
        "trace": add_trace(
            state,
            f"Company and personal KB retrieval → {len(docs)} chunks",
        ),
    }


def grade_kb(state: AgentState):
    grader = llm().with_structured_output(
        EvidenceGrade,
        method="json_mode",
    )

    context = "\n\n".join(
        f"Source: {doc.metadata.get('source', 'unknown')}\n" f"{doc.page_content}"
        for doc in state["kb_docs"]
    )

    grade = grader.invoke(f"""
You are grading evidence for an enterprise HR assistant.

User question:
{state['question']}

Private HR knowledge base:
{context}

If the knowledge base contains information that directly answers the question,
return "good".

For example, if the question asks about remote work and the knowledge base
contains a Remote Work Policy with eligibility, frequency, approval, working
hours, tools, or security requirements, that is sufficient evidence.

Return "weak" only when the knowledge base truly does not contain enough
information to answer the question.

Return only valid JSON:
{{"grade":"good"}}
or
{{"grade":"weak"}}
""")

    return {
        "kb_grade": grade.grade,
        "trace": add_trace(
            state,
            f"KB evidence grade → {grade.grade.upper()}",
        ),
    }


def after_kb(
    state: AgentState,
) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"


def search_web(state: AgentState):
    result = web_search_tool().invoke({"query": state["current_query"]})

    lines = []
    citations = []

    if isinstance(result, dict):
        if result.get("answer"):
            lines.append("Search answer: " + result["answer"])

        for item in result.get("results", []):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")

            lines.append(f"Title: {title}\n" f"URL: {url}\n" f"Content: {content}")

            citations.append(
                {
                    "title": title or url,
                    "url": url,
                    "type": "web",
                }
            )

    else:
        lines.append(str(result))

    return {
        "web_results": "\n\n".join(lines),
        "citations": citations,
        "source_used": "web",
        "trace": add_trace(
            state,
            "Web fallback → Tavily search",
        ),
    }


def grade_web(state: AgentState):
    grader = llm().with_structured_output(
        EvidenceGrade,
        method="json_mode",
    )

    grade = grader.invoke(f"""
You are evaluating web search evidence for an HR employee-support assistant.

User question:
{state['question']}

Web evidence:
{state['web_results']}

Return "good" when at least one result is directly relevant and contains useful
factual information supporting an answer.

Return "weak" only when the results are irrelevant, contradictory, or insufficient.

Return only valid JSON:
{{"grade":"good"}}
or
{{"grade":"weak"}}
""")

    return {
        "web_grade": grade.grade,
        "trace": add_trace(
            state,
            f"Web evidence grade → {grade.grade.upper()}",
        ),
    }


def after_web(
    state: AgentState,
) -> Literal[
    "generate_from_web",
    "rewrite_query",
    "insufficient",
]:
    if state["web_grade"] == "good":
        return "generate_from_web"

    if state["retry_count"] < settings.max_retries:
        return "rewrite_query"

    return "insufficient"


def rewrite_query(state: AgentState):
    rewritten = llm().invoke(f"""
Rewrite this HR/employee-support question for better private knowledge retrieval and public web search.
Preserve intent, add useful HR/policy keywords, do not answer, return only the query.
Question: {state['question']}
""").content.strip()

    return {
        "current_query": rewritten,
        "retry_count": state["retry_count"] + 1,
        "trace": add_trace(
            state,
            f"Query rewrite → {rewritten}",
        ),
    }


def generate_from_kb(state: AgentState):
    context = "\n\n".join(
        f"[Source: {doc.metadata.get('source', 'unknown')}]\n" f"{doc.page_content}"
        for doc in state["kb_docs"]
    )

    answer = llm().invoke(f"""
You are an enterprise HR policy and employee support copilot.
Answer ONLY from the private company HR knowledge base below.
Be concise, practical, respectful, and policy-grounded.
If steps are present, present them clearly.
Do not invent policy details.
Mention that the answer is based on the company's private knowledge base.

Question:
{state['question']}

Private KB:
{context}
""").content

    citations = []
    seen = set()

    for doc in state["kb_docs"]:
        source = doc.metadata.get("source", "Private KB")

        if source not in seen:
            seen.add(source)

            citations.append(
                {
                    "title": source.replace("\\", "/").split("/")[-1],
                    "url": "",
                    "type": "private_kb",
                }
            )

    return {
        "answer": answer,
        "source_used": "private_kb",
        "citations": citations,
        "trace": add_trace(
            state,
            "Answer generation → PRIVATE KB",
        ),
    }


def generate_from_web(state: AgentState):
    answer = llm().invoke(f"""
You are an enterprise HR policy and employee support copilot.
The private company HR knowledge base was insufficient.
Answer ONLY from the web evidence below.
Clearly say this is external public information and may require HR validation
before being treated as company policy or employment guidance.

Question:
{state['question']}

Web evidence:
{state['web_results']}
""").content

    return {
        "answer": answer,
        "source_used": "web_search",
        "trace": add_trace(
            state,
            "Answer generation → WEB SEARCH",
        ),
    }


def direct_answer(state: AgentState):
    answer = (
        llm().invoke("Respond briefly and naturally to: " + state["question"]).content
    )

    return {
        "answer": answer,
        "source_used": "direct",
        "trace": add_trace(
            state,
            "Direct response → no retrieval",
        ),
    }


def insufficient(state: AgentState):
    return {
        "answer": (
            "I couldn't find enough reliable evidence in the company "
            "HR knowledge base or external search to answer confidently. "
            "Please contact the HR team or provide more details."
        ),
        "source_used": "insufficient_evidence",
        "trace": add_trace(
            state,
            "Stopped → insufficient reliable evidence",
        ),
    }


def build_graph():
    graph = StateGraph(AgentState)

    for name, fn in {
        "route_question": route_question,
        "retrieve_kb": retrieve_kb,
        "grade_kb": grade_kb,
        "search_web": search_web,
        "grade_web": grade_web,
        "rewrite_query": rewrite_query,
        "generate_from_kb": generate_from_kb,
        "generate_from_web": generate_from_web,
        "direct_answer": direct_answer,
        "insufficient": insufficient,
    }.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "route_question")

    graph.add_conditional_edges(
        "route_question",
        route_after_router,
        {
            "retrieve_kb": "retrieve_kb",
            "direct_answer": "direct_answer",
        },
    )

    graph.add_edge("retrieve_kb", "grade_kb")

    graph.add_conditional_edges(
        "grade_kb",
        after_kb,
        {
            "generate_from_kb": "generate_from_kb",
            "search_web": "search_web",
        },
    )

    graph.add_edge("search_web", "grade_web")

    graph.add_conditional_edges(
        "grade_web",
        after_web,
        {
            "generate_from_web": "generate_from_web",
            "rewrite_query": "rewrite_query",
            "insufficient": "insufficient",
        },
    )

    graph.add_edge("rewrite_query", "retrieve_kb")
    graph.add_edge("generate_from_kb", END)
    graph.add_edge("generate_from_web", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("insufficient", END)

    return graph.compile()


agent_graph = build_graph()


def ask(question: str):
    initial: AgentState = {
        "question": question,
        "current_query": question,
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
    }

    return agent_graph.invoke(initial)


def _is_simple_greeting(question: str) -> bool:
    normalized = question.lower().strip().strip(" \t\r\n.,!?")

    greetings = {
        "hi",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "hey there",
        "good morning",
        "good afternoon",
        "good evening",
    }

    return normalized in greetings


def answer_guest_question(question: str) -> dict:
    """
    Answer anonymous guests using public web results only.

    Never access Pinecone, internal HR documents, personal documents,
    the conversation database, or the audit database.
    """
    normalized = " ".join(question.lower().split()).strip(" \t\r\n.,!?")

    # Handle simple greetings without invoking web search or the LLM.
    greetings = {
        "hi",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "hey there",
        "good morning",
        "good afternoon",
        "good evening",
    }

    if normalized in greetings:
        return {
            "answer": (
                "Hello! Welcome to HR Mind AI. You can ask me general HR "
                "questions about workplace policies, leave types, onboarding, "
                "employee benefits, and other workplace topics."
            ),
            "source_used": "direct_answer",
            "citations": [],
            "trace": ["Guest mode → greeting handled directly"],
        }
    # Handle identity and basic conversational questions directly.
    direct_answers = {
        "tell me your name": (
            "I'm HR Mind AI, your AI assistant for general HR questions. "
            "You can ask me about onboarding, workplace policies, leave "
            "types, employee benefits, and other HR topics."
        ),
        "what is your name": (
            "My name is HR Mind AI. I'm here to help with HR-related questions."
        ),
        "what's your name": (
            "My name is HR Mind AI. I'm here to help with HR-related questions."
        ),
        "who are you": (
            "I'm HR Mind AI, an AI assistant designed to help answer HR questions."
        ),
        "what are you": ("I'm HR Mind AI, an AI assistant for HR-related questions."),
        "who created you": (
            "I'm HR Mind AI, an AI assistant built for this HR assistance "
            "application. I don't have verified creator details to share."
        ),
        "who made you": (
            "I'm HR Mind AI, an AI assistant built for this HR assistance "
            "application. I don't have verified creator details to share."
        ),
        "who built you": (
            "I'm HR Mind AI, an AI assistant built for this HR assistance "
            "application. I don't have verified creator details to share."
        ),
    }

    if normalized in direct_answers:
        return {
            "answer": direct_answers[normalized],
            "source_used": "direct_answer",
            "citations": [],
            "trace": ["Guest mode → conversational question handled directly"],
        }
    # Do not expose internal company policies to anonymous guests.
    internal_policy_markers = (
        "our company policy",
        "our company",
        "my company policy",
        "my company",
        "our employee handbook",
        "our hr policy",
        "my hr documents",
        "our internal policy",
        "company-specific policy",
    )

    if any(marker in normalized for marker in internal_policy_markers):
        return {
            "answer": (
                "I can help with general HR questions as a guest, but I "
                "cannot access internal company policies or employee "
                "documents in guest mode. For a specific company policy, "
                "please sign in to use the authorized company HR knowledge base."
            ),
            "source_used": "insufficient_evidence",
            "citations": [],
            "trace": ["Guest access → internal data unavailable"],
        }

    # Search public sources only. Do not retrieve internal or personal documents.
    try:
        result = web_search_tool().invoke({"query": question})
    except Exception:
        return {
            "answer": (
                "I'm having trouble searching public sources right now. "
                "Please try again shortly, or rephrase your question."
            ),
            "source_used": "insufficient_evidence",
            "citations": [],
            "trace": ["Guest search → public search unavailable"],
        }

    snippets = []
    citations = []

    if isinstance(result, dict):
        answer_snippet = result.get("answer")

        if answer_snippet:
            snippets.append(str(answer_snippet))

        for item in result.get("results", []):
            if not isinstance(item, dict):
                continue

            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            content = str(item.get("content", "")).strip()

            if content:
                snippets.append(f"Title: {title}\nContent: {content}\nURL: {url}")

            if url.startswith(("https://", "http://")):
                citations.append(
                    {
                        "title": title or url,
                        "url": url,
                        "type": "web",
                    }
                )
    else:
        text_result = str(result).strip()

        if text_result:
            snippets.append(text_result)

    if not snippets:
        return {
            "answer": (
                "I couldn't find enough reliable public information to "
                "answer this question. Please try rephrasing it."
            ),
            "source_used": "insufficient_evidence",
            "citations": [],
            "trace": ["Guest search → no useful public evidence"],
        }

    prompt = f"""
You are HR Mind AI, answering a guest who has not signed in.

Guests can ask general HR questions without signing in. Do not tell
guests that sign-in is required merely to discuss general HR topics.

Use ONLY the public web search evidence below. You do not have access
to internal company documents or employee-private information.

Rules:
- Answer general HR questions clearly, helpfully, and naturally.
- Keep greetings and simple conversational responses brief.
- Never claim to know the guest's company's internal policies.
- If asked about a specific company's internal policy, explain that
  it cannot be verified from public information.
- If public sources conflict, explain the discrepancy.
- For country-specific employment rules, identify the jurisdiction
  when the evidence makes it clear.
- Do not make up facts or invent citations.
- Do not present external information as the guest's company policy.
- Explain uncertainty when search evidence is insufficient.
- Do not ask for passwords, employee records, or sensitive personal data.
- Never invent or guess your creator, owner, developer, or company affiliation.
- Never claim to be built by Amazon or any other organization unless
  that information is explicitly established by trusted application context.
- For greetings, identity questions, and simple conversational messages,
  do not use web search results as evidence of your own identity.

Question:
{question}

Public web search evidence:
{chr(10).join(snippets)}
"""

    try:
        answer = llm().invoke(prompt).content
    except Exception:
        return {
            "answer": (
                "I'm having trouble generating a response right now. "
                "Please try again shortly."
            ),
            "source_used": "insufficient_evidence",
            "citations": [],
            "trace": ["Guest search → answer generation failed"],
        }

    return {
        "answer": str(answer),
        "source_used": "web_search",
        "citations": citations,
        "trace": [
            "Guest mode → public web search only",
            "Answer generated → external evidence",
        ],
    }

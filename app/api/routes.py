import json
import logging
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.rag.vectorstore import add_documents, delete_vectors
from app.rag.workflow import agent_graph, answer_guest_question
from app.services.audit import write_audit
from app.services.authentication import (
    create_company_document,
    create_personal_document,
    delete_company_document_record,
    delete_personal_document_record,
    get_company_document,
    get_current_user,
    get_personal_document,
    list_company_documents,
    list_personal_documents,
    require_admin,
)
from app.services.conversations import (
    create_conversation,
    create_share_link,
    delete_conversation,
    get_conversation,
    get_messages,
    get_shared_conversation,
    list_conversations,
    list_share_links,
    rename_conversation,
    revoke_share_link,
    save_message,
)
from app.services.ingestion import SUPPORTED, chunk_documents, load_file

router = APIRouter(prefix="/api")

settings = get_settings()
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    conversation_id: str | None = None


class GuestChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1500)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


def _markdown_filename(title: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-_")[:60]
    return f"{stem or 'hr-mind-ai-conversation'}.md"


def _citation_markdown(citation: dict) -> str:
    title = str(citation.get("title") or "Source")
    title = title.replace("[", "\\[").replace("]", "\\]")

    url = str(citation.get("url") or "").strip()
    page = citation.get("page")

    page_text = f", page {page}" if page not in (None, "") else ""

    if url.startswith(("https://", "http://")):
        safe_url = url.replace(")", "%29")
        label = f"[{title}]({safe_url})"
    else:
        label = title

    line = f"- {label}{page_text}"

    excerpt = str(citation.get("excerpt") or "").strip()

    if excerpt:
        excerpt = excerpt.replace("\n", " ")
        line += f"\n  > {excerpt}"

    return line


def _conversation_as_markdown(
    conversation: dict,
    messages: list[dict],
) -> str:
    from datetime import datetime, timezone

    title = str(conversation.get("title") or "HR Mind AI conversation")
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# {title}",
        "",
        f"Exported from HR Mind AI on {exported_at}",
        "",
    ]

    for message in messages:
        role = "You" if message.get("role") == "user" else "HR Mind AI"
        content = str(message.get("content") or "").strip()

        lines.extend(
            [
                f"## {role}",
                "",
                content,
                "",
            ]
        )

        citations = message.get("citations", [])

        if isinstance(citations, list) and citations:
            lines.extend(["### Sources", ""])

            for citation in citations:
                if isinstance(citation, dict):
                    lines.append(_citation_markdown(citation))

            lines.append("")

        lines.extend(["---", ""])

    return "\n".join(lines).strip() + "\n"


def _safe_filename(filename: str | None) -> str:
    raw_name = Path((filename or "document").replace("\\", "/")).name

    suffix = Path(raw_name).suffix.lower()

    if suffix not in SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=("Supported file types: " f"{', '.join(sorted(SUPPORTED))}"),
        )

    stem = (
        re.sub(
            r"[^\w.-]+",
            "_",
            Path(raw_name).stem,
        ).strip(
            "._"
        )[:100]
        or "document"
    )

    return f"{stem}{suffix}"


async def _save_upload(
    file: UploadFile,
    destination: Path,
) -> int:
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    written = 0

    try:
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_BYTES)

                if not chunk:
                    break

                written += len(chunk)

                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Maximum upload size is 10 MB.",
                    )

                output.write(chunk)

        if written == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is empty.",
            )

        return written

    except Exception:
        destination.unlink(missing_ok=True)
        raise

    finally:
        await file.close()


def _load_and_chunk(path: Path):
    try:
        documents = load_file(path)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from None

    for document in documents:
        document.metadata["source"] = path.name

    chunks = chunk_documents(documents)

    if not chunks:
        raise HTTPException(
            status_code=400,
            detail="No readable text was found in this document.",
        )

    return chunks


def _remove_upload_files(destination: Path) -> None:
    destination.unlink(missing_ok=True)
    shutil.rmtree(
        destination.parent,
        ignore_errors=True,
    )


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": settings.app_name,
    }


# ---------------------------------------------------------
# Conversation history: authenticated account ownership
# ---------------------------------------------------------


@router.get("/conversations")
def conversations(request: Request):
    user = get_current_user(request)

    return {"conversations": list_conversations(user["id"])}


@router.post("/conversations", status_code=201)
def new_conversation(request: Request):
    user = get_current_user(request)

    conversation = create_conversation(user["id"])

    return {
        "conversation": {
            key: value for key, value in conversation.items() if key != "owner_id"
        }
    }


@router.get("/conversations/{conversation_id}")
def open_conversation(
    conversation_id: str,
    request: Request,
):
    user = get_current_user(request)

    conversation = get_conversation(
        user["id"],
        conversation_id,
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {
        "conversation": conversation,
        "messages": get_messages(
            user["id"],
            conversation_id,
        ),
    }


@router.get("/conversations/{conversation_id}/export")
def export_conversation(
    conversation_id: str,
    request: Request,
):
    """Download an owned conversation as a Markdown file."""
    user = get_current_user(request)

    conversation = get_conversation(user["id"], conversation_id)

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    messages = get_messages(user["id"], conversation_id)
    markdown = _conversation_as_markdown(conversation, messages)
    filename = _markdown_filename(str(conversation.get("title") or "conversation"))

    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/conversations/{conversation_id}/share",
    status_code=201,
)
def create_conversation_share(
    conversation_id: str,
    request: Request,
):
    """Create a seven-day, read-only snapshot share link."""
    user = get_current_user(request)

    share = create_share_link(user["id"], conversation_id)

    if not share:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {
        "share_id": share["share_id"],
        "path": f"/share/{share['token']}",
        "created_at": share["created_at"],
        "expires_at": share["expires_at"],
        "expires_in_days": 7,
        "snapshot": True,
    }


@router.get("/conversations/{conversation_id}/shares")
def conversation_share_links(
    conversation_id: str,
    request: Request,
):
    user = get_current_user(request)

    shares = list_share_links(
        user["id"],
        conversation_id,
    )

    if shares is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {"shares": shares}


@router.delete("/conversations/{conversation_id}/shares/{share_id}")
def delete_conversation_share(
    conversation_id: str,
    share_id: str,
    request: Request,
):
    user = get_current_user(request)

    revoked = revoke_share_link(
        user["id"],
        conversation_id,
        share_id,
    )

    if not revoked:
        raise HTTPException(
            status_code=404,
            detail="Share link not found or already revoked.",
        )

    return {
        "revoked": True,
        "share_id": share_id,
    }


@router.get("/shared/{token}")
def read_shared_conversation(
    token: str,
    response: Response,
):
    """Read a shared snapshot without account or agent-trace information."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"

    shared = get_shared_conversation(token)

    if not shared:
        raise HTTPException(
            status_code=404,
            detail="This shared conversation is unavailable or has expired.",
            headers={"Cache-Control": "no-store"},
        )

    return {
        "conversation": shared.get("conversation", {}),
        "messages": shared.get("messages", []),
        "share_created_at": shared.get("share_created_at"),
        "expires_at": shared.get("expires_at"),
    }


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
    request: Request,
):
    user = get_current_user(request)

    try:
        updated = rename_conversation(
            user["id"],
            conversation_id,
            payload.title,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from None

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {
        "conversation": get_conversation(
            user["id"],
            conversation_id,
        )
    }


@router.delete("/conversations/{conversation_id}")
def remove_conversation(
    conversation_id: str,
    request: Request,
):
    user = get_current_user(request)

    deleted = delete_conversation(
        user["id"],
        conversation_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {
        "deleted": True,
        "conversation_id": conversation_id,
    }


# ---------------------------------------------------------
# Chat: only access conversations owned by the signed-in user
# ---------------------------------------------------------


@router.post("/chat")
def chat(
    payload: ChatRequest,
    request: Request,
):
    user = get_current_user(request)
    conversation_id = payload.conversation_id

    if not conversation_id or not get_conversation(
        user["id"],
        conversation_id,
    ):
        raise HTTPException(
            status_code=404,
            detail="Choose a valid conversation before chatting.",
        )

    def generate():
        try:
            save_message(
                user["id"],
                conversation_id,
                "user",
                payload.question,
            )

            initial = {
                "question": payload.question,
                "current_query": payload.question,
                "user_id": user["id"],
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

            final_state = {}

            for update in agent_graph.stream(
                initial,
                stream_mode="updates",
            ):
                for node_name, node_update in update.items():
                    final_state.update(node_update)

                    yield json.dumps(
                        {
                            "type": "trace",
                            "node": node_name,
                        }
                    ) + "\n"

            answer = final_state.get("answer", "")
            source_used = final_state.get("source_used", "")
            trace_items = final_state.get("trace", [])
            citations = final_state.get("citations", [])

            try:
                write_audit(
                    payload.question,
                    source_used,
                    trace_items,
                )
            except Exception:
                logger.exception("Failed to write chat audit record")

            save_message(
                user["id"],
                conversation_id,
                "assistant",
                answer,
                source_used,
                citations,
                trace_items,
            )

            yield json.dumps(
                {
                    "type": "final",
                    "answer": answer,
                    "source_used": source_used,
                    "trace": trace_items,
                    "citations": citations,
                    "rewritten_query": final_state.get(
                        "current_query",
                        payload.question,
                    ),
                }
            ) + "\n"

        except Exception:
            logger.exception("Agent chat request failed")

            yield json.dumps(
                {
                    "type": "error",
                    "detail": (
                        "The agent could not complete this request. "
                        "Please try again."
                    ),
                }
            ) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------
# Company-wide documents: administrators only
# ---------------------------------------------------------


@router.get("/documents/company")
def company_documents(request: Request):
    user = get_current_user(request)
    require_admin(user)

    return {"documents": list_company_documents()}


@router.post("/ingest", status_code=201)
async def ingest_company_document(
    request: Request,
    file: UploadFile = File(...),
):
    user = get_current_user(request)
    require_admin(user)

    filename = _safe_filename(file.filename)
    document_id = uuid.uuid4().hex

    destination = (
        Path(settings.upload_dir).resolve() / "company" / document_id / filename
    )

    size_bytes = await _save_upload(file, destination)
    vector_ids: list[str] = []

    try:
        chunks = _load_and_chunk(destination)
        vector_ids = [uuid.uuid4().hex for _ in chunks]

        add_documents(
            chunks,
            namespace=settings.pinecone_namespace,
            ids=vector_ids,
        )

        create_company_document(
            document_id,
            filename,
            str(destination),
            vector_ids,
            size_bytes,
            user["id"],
        )

    except Exception as exc:
        logger.exception(
            "Company document ingestion failed: %s",
            filename,
        )

        rollback_failed = False

        if vector_ids:
            try:
                delete_vectors(
                    vector_ids,
                    namespace=settings.pinecone_namespace,
                )
            except Exception:
                rollback_failed = True
                logger.exception(
                    "Company vector rollback failed. "
                    "Document ID: %s, vector IDs: %s",
                    document_id,
                    vector_ids,
                )

        try:
            _remove_upload_files(destination)
        except Exception:
            rollback_failed = True
            logger.exception(
                "Company upload cleanup failed: %s",
                document_id,
            )

        if rollback_failed:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Upload failed and cleanup could not be completed. "
                    "Check the server logs before retrying."
                ),
            ) from None

        if isinstance(exc, HTTPException):
            raise

        raise HTTPException(
            status_code=500,
            detail=(
                "The document could not be indexed. " "Check server logs and try again."
            ),
        ) from None

    return {
        "message": "Company document indexed",
        "id": document_id,
        "file": filename,
        "chunks": len(chunks),
    }


@router.delete("/documents/company/{document_id}")
def remove_company_document(
    document_id: str,
    request: Request,
):
    user = get_current_user(request)
    require_admin(user)

    record = get_company_document(document_id)

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Company document not found.",
        )

    try:
        delete_vectors(
            record["vector_ids"],
            namespace=settings.pinecone_namespace,
        )

        _remove_upload_files(Path(record["storage_path"]))

        delete_company_document_record(document_id)

    except Exception:
        logger.exception(
            "Could not delete company document %s",
            document_id,
        )

        raise HTTPException(
            status_code=503,
            detail=("The document could not be fully deleted. " "Check server logs."),
        ) from None

    return {
        "deleted": True,
        "id": document_id,
    }


# ---------------------------------------------------------
# Personal documents: owner-only access
# ---------------------------------------------------------


@router.get("/documents/personal")
def personal_documents(request: Request):
    user = get_current_user(request)

    return {"documents": list_personal_documents(user["id"])}


@router.post("/documents/personal", status_code=201)
async def ingest_personal_document(
    request: Request,
    file: UploadFile = File(...),
):
    user = get_current_user(request)

    filename = _safe_filename(file.filename)
    document_id = uuid.uuid4().hex
    namespace = f"user-{user['id']}"

    destination = (
        Path(settings.upload_dir).resolve()
        / "users"
        / user["id"]
        / document_id
        / filename
    )

    size_bytes = await _save_upload(file, destination)
    vector_ids: list[str] = []

    try:
        chunks = _load_and_chunk(destination)
        vector_ids = [uuid.uuid4().hex for _ in chunks]

        add_documents(
            chunks,
            namespace=namespace,
            ids=vector_ids,
        )

        create_personal_document(
            document_id,
            user["id"],
            filename,
            str(destination),
            namespace,
            vector_ids,
            size_bytes,
        )

    except Exception as exc:
        logger.exception(
            "Personal document ingestion failed for user %s",
            user["id"],
        )

        rollback_failed = False

        if vector_ids:
            try:
                delete_vectors(
                    vector_ids,
                    namespace=namespace,
                )
            except Exception:
                rollback_failed = True
                logger.exception(
                    "Private vector rollback failed. "
                    "User ID: %s, document ID: %s, vector IDs: %s",
                    user["id"],
                    document_id,
                    vector_ids,
                )

        try:
            _remove_upload_files(destination)
        except Exception:
            rollback_failed = True
            logger.exception(
                "Private upload cleanup failed for user %s, document %s",
                user["id"],
                document_id,
            )

        if rollback_failed:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Upload failed and cleanup could not be completed. "
                    "Check the server logs before retrying."
                ),
            ) from None

        if isinstance(exc, HTTPException):
            raise

        raise HTTPException(
            status_code=500,
            detail=("The private document could not be indexed. " "Please try again."),
        ) from None

    return {
        "message": "Private document indexed",
        "id": document_id,
        "file": filename,
        "chunks": len(chunks),
    }


@router.delete("/documents/personal/{document_id}")
def remove_personal_document(
    document_id: str,
    request: Request,
):
    user = get_current_user(request)

    record = get_personal_document(
        document_id,
        user["id"],
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Private document not found.",
        )

    try:
        delete_vectors(
            record["vector_ids"],
            namespace=record["namespace"],
        )

        _remove_upload_files(Path(record["storage_path"]))

        delete_personal_document_record(
            document_id,
            user["id"],
        )

    except Exception:
        logger.exception(
            "Could not delete private document %s",
            document_id,
        )

        raise HTTPException(
            status_code=503,
            detail=("The document could not be fully deleted. " "Check server logs."),
        ) from None

    return {
        "deleted": True,
        "id": document_id,
    }


# ---------------------------------------------------------
# Guest chat: public web only, no stored conversation history
# ---------------------------------------------------------

GUEST_REQUESTS_PER_MINUTE = 10
_guest_request_times: dict[str, list[float]] = {}
_guest_rate_lock = threading.Lock()


def _check_guest_rate_limit(request: Request) -> None:
    # Use the direct client address, not an untrusted forwarded header.
    client_ip = request.client.host if request.client else "unknown"

    now = time.monotonic()
    cutoff = now - 60

    with _guest_rate_lock:
        previous = [
            value for value in _guest_request_times.get(client_ip, []) if value > cutoff
        ]

        if len(previous) >= GUEST_REQUESTS_PER_MINUTE:
            _guest_request_times[client_ip] = previous

            raise HTTPException(
                status_code=429,
                detail=(
                    "Guest request limit reached. " "Please try again in a minute."
                ),
            )

        previous.append(now)
        _guest_request_times[client_ip] = previous


@router.post("/guest/chat")
def guest_chat(
    payload: GuestChatRequest,
    request: Request,
):
    _check_guest_rate_limit(request)

    def generate():
        try:
            yield json.dumps(
                {
                    "type": "trace",
                    "node": "guest_public_search",
                }
            ) + "\n"

            result = answer_guest_question(payload.question)

            for step in result.get("trace", []):
                yield json.dumps(
                    {
                        "type": "trace",
                        "node": step,
                    }
                ) + "\n"

            yield json.dumps(
                {
                    "type": "final",
                    "answer": result["answer"],
                    "source_used": result["source_used"],
                    "citations": result.get("citations", []),
                    "trace": result.get("trace", []),
                }
            ) + "\n"

        except Exception:
            logger.exception("Guest chat request failed")

            yield json.dumps(
                {
                    "type": "error",
                    "detail": (
                        "Guest search is temporarily unavailable. "
                        "Please try again later."
                    ),
                }
            ) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )

import json
import logging
import re
import shutil
import threading
import time
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

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
    list_all_documents_for_admin,
    list_company_documents,
    get_personal_document_for_admin,
    list_personal_documents,
    update_company_document_vectors,
    update_personal_document_vectors,
    get_company_policy_metadata,
    list_company_policy_documents,
    save_company_policy_metadata,
    get_company_policy_metadata_map,
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
_active_chat_cancellations: dict[str, tuple[threading.Event, str]] = {}
_chat_cancellation_lock = threading.Lock()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    conversation_id: str | None = None
    request_id: str | None = Field(default=None, min_length=16, max_length=80)


class GuestChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1500)
    request_id: str | None = Field(default=None, min_length=16, max_length=80)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class PolicyMetadataUpdate(BaseModel):
    policy_family: str = Field(default="", max_length=120)
    version_label: str = Field(default="", max_length=40)
    policy_status: str = Field(min_length=1, max_length=20)
    effective_date: str = Field(default="", max_length=10)
    supersedes_document_id: str | None = Field(default=None, max_length=80)


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


def _reindex_saved_document(
    record: dict,
    namespace: str,
    update_record_vectors,
    metadata_overrides: dict | None = None,
) -> dict:
    """Rebuild stored chunks; optionally tag them with stable document metadata."""
    upload_root = Path(settings.upload_dir).resolve()
    stored_path = Path(record["storage_path"]).resolve()

    if not stored_path.is_relative_to(upload_root) or not stored_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="The original uploaded file is missing or unavailable.",
        )

    chunks = _load_and_chunk(stored_path)
    if metadata_overrides:
        for chunk in chunks:
            chunk.metadata.update(metadata_overrides)
    old_vector_ids = list(record.get("vector_ids") or [])
    new_vector_ids = [uuid.uuid4().hex for _ in chunks]
    record_updated = False

    try:
        add_documents(chunks, namespace=namespace, ids=new_vector_ids)
        update_record_vectors(new_vector_ids)
        record_updated = True
        if old_vector_ids:
            delete_vectors(old_vector_ids, namespace=namespace)
    except Exception:
        logger.exception("Document re-index failed for document %s", record.get("id"))
        if record_updated:
            try:
                update_record_vectors(old_vector_ids)
            except Exception:
                logger.exception("Could not restore prior vector IDs for document %s", record.get("id"))
        try:
            if new_vector_ids:
                delete_vectors(new_vector_ids, namespace=namespace)
        except Exception:
            logger.exception("Could not remove replacement vectors for document %s", record.get("id"))
        raise HTTPException(
            status_code=503,
            detail="The document could not be re-indexed. Check server logs and try again.",
        ) from None

    return {
        "reindexed": True,
        "id": record["id"],
        "file": record["filename"],
        "chunks": len(chunks),
        "index_status": "indexed",
    }

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


def _register_chat_cancellation(
    request_id: str | None,
    owner_key: str,
):
    request_id = request_id or uuid.uuid4().hex

    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
        raise HTTPException(status_code=400, detail="Invalid request ID.")

    event = threading.Event()

    with _chat_cancellation_lock:
        if request_id in _active_chat_cancellations:
            raise HTTPException(
                status_code=409,
                detail="Request ID is already active.",
            )

        _active_chat_cancellations[request_id] = (event, owner_key)

    return request_id, event


def _signal_chat_cancellation(
    request_id: str,
    owner_key: str,
) -> bool:
    with _chat_cancellation_lock:
        active = _active_chat_cancellations.get(request_id)

        if active is None or active[1] != owner_key:
            return False

        active[0].set()
        return True


def _cleanup_chat_cancellation(
    request_id: str,
    event: threading.Event,
) -> None:
    event.set()

    with _chat_cancellation_lock:
        active = _active_chat_cancellations.get(request_id)

        if active is not None and active[0] is event:
            _active_chat_cancellations.pop(request_id, None)


def _guest_cancel_owner(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    return f"guest:{client_ip}"


@router.post("/chat/{request_id}/cancel")
def cancel_chat_generation(request_id: str, request: Request):
    user = get_current_user(request)

    cancelled = _signal_chat_cancellation(
        request_id,
        f"user:{user['id']}",
    )

    return {"cancelled": cancelled}


@router.post("/guest/chat/{request_id}/cancel")
def cancel_guest_chat_generation(request_id: str, request: Request):
    cancelled = _signal_chat_cancellation(
        request_id,
        _guest_cancel_owner(request),
    )

    return {"cancelled": cancelled}


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

    request_id, cancel_event = _register_chat_cancellation(
        payload.request_id,
        f"user:{user['id']}",
    )

    def generate():
        user_message_saved = False
        assistant_message_saved = False
        final_state = {}

        try:
            save_message(
                user["id"],
                conversation_id,
                "user",
                payload.question,
            )
            user_message_saved = True

            if cancel_event.is_set():
                yield json.dumps({"type": "cancelled"}) + "\n"
                return

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

            for update in agent_graph.stream(
                initial,
                stream_mode="updates",
            ):
                if cancel_event.is_set():
                    break

                for node_name, node_update in update.items():
                    if cancel_event.is_set():
                        break

                    final_state.update(node_update)

                    yield json.dumps(
                        {
                            "type": "trace",
                            "node": node_name,
                        }
                    ) + "\n"

                if cancel_event.is_set():
                    break

            if cancel_event.is_set():
                save_message(
                    user["id"],
                    conversation_id,
                    "assistant",
                    "Response generation was stopped by the user.",
                    "cancelled",
                    [],
                    final_state.get("trace", []),
                )
                assistant_message_saved = True

                yield json.dumps({"type": "cancelled"}) + "\n"
                return

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

            if cancel_event.is_set():
                save_message(
                    user["id"],
                    conversation_id,
                    "assistant",
                    "Response generation was stopped by the user.",
                    "cancelled",
                    [],
                    trace_items,
                )
                assistant_message_saved = True

                yield json.dumps({"type": "cancelled"}) + "\n"
                return

            save_message(
                user["id"],
                conversation_id,
                "assistant",
                answer,
                source_used,
                citations,
                trace_items,
            )
            assistant_message_saved = True

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

        except GeneratorExit:
            cancel_event.set()
            raise

        except Exception:
            if not cancel_event.is_set():
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

        finally:
            if (
                cancel_event.is_set()
                and user_message_saved
                and not assistant_message_saved
            ):
                try:
                    save_message(
                        user["id"],
                        conversation_id,
                        "assistant",
                        "Response generation was stopped by the user.",
                        "cancelled",
                        [],
                        final_state.get("trace", []),
                    )
                except Exception:
                    logger.exception("Could not save cancelled-generation status")

    def cleanup():
        _cleanup_chat_cancellation(request_id, cancel_event)

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(cleanup),
    )


# ---------------------------------------------------------
# Company-wide documents: administrators only
# ---------------------------------------------------------


@router.get("/admin/documents")
def admin_all_documents(request: Request):
    """Return company and private uploads to authenticated administrators only."""
    user = get_current_user(request)
    require_admin(user)
    return {"documents": list_all_documents_for_admin()}




# HRMIND_AI_POLICY_LIFECYCLE_V1
@router.get("/admin/policy-documents")
def admin_policy_documents(request: Request):
    """Return company policies and flag ambiguous active-version metadata."""
    user = get_current_user(request)
    require_admin(user)

    documents = list_company_policy_documents()
    active_by_family: dict[str, list[dict]] = {}
    for doc in documents:
        family = " ".join(str(doc.get("policy_family") or "").split()).casefold()
        if family and doc.get("policy_status") == "active":
            active_by_family.setdefault(family, []).append(doc)

    conflicts = []
    for family_key, active_docs in active_by_family.items():
        if len(active_docs) > 1:
            family_label = next(
                (str(doc.get("policy_family") or "").strip() for doc in active_docs if doc.get("policy_family")),
                family_key,
            )
            conflicts.append({
                "policy_family": family_label,
                "active_count": len(active_docs),
                "documents": [
                    {"id": doc["id"], "filename": doc["filename"], "version_label": doc.get("version_label", "")}
                    for doc in active_docs
                ],
                "message": (
                    f'{len(active_docs)} documents are marked Active for "{family_label}". '
                    "Confirm the authoritative version or mark older versions Superseded."
                ),
            })

    for doc in documents:
        family_key = " ".join(str(doc.get("policy_family") or "").split()).casefold()
        doc["has_active_conflict"] = bool(family_key and len(active_by_family.get(family_key, [])) > 1)

    return {"documents": documents, "conflicts": conflicts}


@router.put("/admin/policy-documents/{document_id}")
def update_admin_policy_metadata(
    document_id: str,
    payload: PolicyMetadataUpdate,
    request: Request,
):
    """Save lifecycle metadata, re-index vectors, and optionally supersede an older version."""
    user = get_current_user(request)
    require_admin(user)

    record = get_company_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Company document not found.")

    family = " ".join(payload.policy_family.strip().split())
    version = payload.version_label.strip()
    status = payload.policy_status.strip().lower()
    effective_date = payload.effective_date.strip()
    supersedes_id = (payload.supersedes_document_id or "").strip() or None

    if status not in {"draft", "active", "superseded"}:
        raise HTTPException(status_code=422, detail="Select a valid policy lifecycle status.")
    if status == "active" and (not family or not version or not effective_date):
        raise HTTPException(
            status_code=422,
            detail="An Active policy requires a policy family, version label, and effective date.",
        )
    if effective_date:
        try:
            date.fromisoformat(effective_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Effective date must use YYYY-MM-DD.") from None

    superseded_record = None
    old_superseded_metadata = None
    if supersedes_id:
        if status != "active":
            raise HTTPException(status_code=422, detail="Only an Active policy can supersede another version.")
        if supersedes_id == document_id:
            raise HTTPException(status_code=422, detail="A document cannot supersede itself.")
        superseded_record = get_company_document(supersedes_id)
        if not superseded_record:
            raise HTTPException(status_code=404, detail="The selected previous policy version was not found.")
        old_superseded_metadata = get_company_policy_metadata(supersedes_id)
        old_family = " ".join(str(old_superseded_metadata.get("policy_family") or "").split()).casefold()
        if old_family and old_family != family.casefold():
            raise HTTPException(
                status_code=422,
                detail="The superseded document belongs to a different policy family.",
            )
        if old_superseded_metadata.get("supersedes_document_id") == document_id:
            raise HTTPException(status_code=422, detail="This supersession would create a version cycle.")

    new_metadata = {
        "policy_family": family,
        "version_label": version,
        "policy_status": status,
        "effective_date": effective_date,
        "supersedes_document_id": supersedes_id,
    }
    old_metadata = get_company_policy_metadata(document_id)

    try:
        save_company_policy_metadata(document_id, new_metadata, user["id"])
        _reindex_saved_document(
            record,
            settings.pinecone_namespace,
            lambda vector_ids: update_company_document_vectors(document_id, vector_ids),
            metadata_overrides={
                "hrmindai_document_id": document_id,
                "hrmindai_document_kind": "company",
            },
        )

        if supersedes_id and superseded_record is not None and old_superseded_metadata is not None:
            superseded_metadata = {
                "policy_family": family,
                "version_label": str(old_superseded_metadata.get("version_label") or ""),
                "policy_status": "superseded",
                "effective_date": str(old_superseded_metadata.get("effective_date") or ""),
                "supersedes_document_id": old_superseded_metadata.get("supersedes_document_id"),
            }
            save_company_policy_metadata(supersedes_id, superseded_metadata, user["id"])
            _reindex_saved_document(
                superseded_record,
                settings.pinecone_namespace,
                lambda vector_ids: update_company_document_vectors(supersedes_id, vector_ids),
                metadata_overrides={
                    "hrmindai_document_id": supersedes_id,
                    "hrmindai_document_kind": "company",
                },
            )
    except Exception as exc:
        # Restore the metadata decision if vector re-indexing fails. The re-index
        # helper itself preserves/rolls back the stored vector IDs on failure.
        try:
            save_company_policy_metadata(document_id, old_metadata, user["id"])
            if supersedes_id and old_superseded_metadata is not None:
                save_company_policy_metadata(supersedes_id, old_superseded_metadata, user["id"])
        except Exception:
            logger.exception("Policy metadata rollback failed for document %s", document_id)
        if isinstance(exc, HTTPException):
            raise
        logger.exception("Policy lifecycle update failed for document %s", document_id)
        raise HTTPException(
            status_code=503,
            detail="Policy metadata could not be applied safely. Review server logs before retrying.",
        ) from None

    logger.info(
        "Admin updated policy lifecycle: admin_id=%s document_id=%s status=%s family=%s version=%s supersedes=%s",
        user["id"], document_id, status, family, version, supersedes_id,
    )
    return {
        "saved": True,
        "document_id": document_id,
        "policy_status": status,
        "message": (
            "Policy metadata saved and vectors refreshed."
            if not supersedes_id
            else "New active version saved; the selected previous version was marked Superseded and re-indexed."
        ),
    }

@router.delete("/admin/documents/{kind}/{document_id}")
def admin_delete_any_document(kind: str, document_id: str, request: Request):
    """Delete any company or personal document; administrators only."""
    user = get_current_user(request)
    require_admin(user)

    if kind == "company":
        record = get_company_document(document_id)
        namespace = settings.pinecone_namespace
    elif kind == "personal":
        record = get_personal_document_for_admin(document_id)
        namespace = record.get("namespace") if record else None
    else:
        raise HTTPException(status_code=400, detail="Unsupported document type.")

    if not record:
        raise HTTPException(status_code=404, detail="Document not found.")

    upload_root = Path(settings.upload_dir).resolve()
    storage_path = Path(record["storage_path"]).resolve()
    if not storage_path.is_relative_to(upload_root):
        logger.error(
            "Admin document deletion blocked for unsafe path: admin_id=%s kind=%s document_id=%s",
            user["id"], kind, document_id,
        )
        raise HTTPException(
            status_code=500,
            detail="The stored document path is outside the upload directory. Deletion was stopped.",
        )

    filename = record.get("filename", "Document")
    uploader_id = record.get("uploaded_by") or record.get("user_id")
    vector_ids = record.get("vector_ids") or []

    try:
        if vector_ids:
            delete_vectors(vector_ids, namespace=namespace)

        _remove_upload_files(storage_path)

        if kind == "company":
            delete_company_document_record(document_id)
        else:
            # Reuse the owner-scoped record deletion with the owner ID from
            # the row fetched only after require_admin() succeeds.
            delete_personal_document_record(document_id, record["user_id"])
    except Exception:
        logger.exception(
            "Admin could not fully delete document: admin_id=%s kind=%s document_id=%s",
            user["id"], kind, document_id,
        )
        raise HTTPException(
            status_code=503,
            detail="The document could not be fully deleted. Check server logs before retrying.",
        ) from None

    logger.info(
        "Admin deleted document: admin_id=%s kind=%s document_id=%s uploader_id=%s filename=%s",
        user["id"], kind, document_id, uploader_id, filename,
    )
    return {
        "deleted": True,
        "id": document_id,
        "kind": kind,
        "filename": filename,
    }

@router.get("/documents/company")
def company_documents(request: Request):
    user = get_current_user(request)
    require_admin(user)

    return {"documents": list_company_documents()}


@router.post("/documents/company/{document_id}/reindex")
def reindex_company_document(document_id: str, request: Request):
    user = get_current_user(request)
    require_admin(user)
    record = get_company_document(document_id)

    if not record:
        raise HTTPException(status_code=404, detail="Company document not found.")

    return _reindex_saved_document(
        record,
        settings.pinecone_namespace,
        lambda vector_ids: update_company_document_vectors(document_id, vector_ids),
        metadata_overrides={
            "hrmindai_document_id": document_id,
            "hrmindai_document_kind": "company",
        },
    )

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
        for chunk in chunks:
            chunk.metadata.update({
                "hrmindai_document_id": document_id,
                "hrmindai_document_kind": "company",
            })
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


@router.post("/documents/personal/{document_id}/reindex")
def reindex_personal_document(document_id: str, request: Request):
    user = get_current_user(request)
    record = get_personal_document(document_id, user["id"])

    if not record:
        raise HTTPException(status_code=404, detail="Private document not found.")

    return _reindex_saved_document(
        record,
        record["namespace"],
        lambda vector_ids: update_personal_document_vectors(
            document_id, user["id"], vector_ids
        ),
    )

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

    request_id, cancel_event = _register_chat_cancellation(
        payload.request_id,
        _guest_cancel_owner(request),
    )

    def generate():
        try:
            yield json.dumps(
                {
                    "type": "trace",
                    "node": "guest_public_search",
                }
            ) + "\n"

            if cancel_event.is_set():
                yield json.dumps({"type": "cancelled"}) + "\n"
                return

            result = answer_guest_question(payload.question)

            if cancel_event.is_set():
                yield json.dumps({"type": "cancelled"}) + "\n"
                return

            for step in result.get("trace", []):
                if cancel_event.is_set():
                    yield json.dumps({"type": "cancelled"}) + "\n"
                    return

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

        except GeneratorExit:
            cancel_event.set()
            raise

        except Exception:
            if not cancel_event.is_set():
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

    def cleanup():
        _cleanup_chat_cancellation(request_id, cancel_event)

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(cleanup),
    )

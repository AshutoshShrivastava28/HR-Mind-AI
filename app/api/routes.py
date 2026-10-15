import hmac
import json
import logging
import re
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.rag.vectorstore import add_documents
from app.rag.workflow import agent_graph
from app.services.audit import write_audit
from app.services.conversations import (
    create_conversation,
    delete_conversation,
    get_conversation,
    get_messages,
    list_conversations,
    rename_conversation,
    save_message,
)
from app.services.ingestion import SUPPORTED, chunk_documents, load_file

router = APIRouter(prefix="/api")

settings = get_settings()
logger = logging.getLogger(__name__)

OWNER_COOKIE = "hrmind_owner"
DEFAULT_ADMIN_KEY = "change-me-in-production"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_READ_CHUNK_BYTES = 64 * 1024


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    conversation_id: str | None = None


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


def resolve_owner(request: Request) -> tuple[str, bool]:
    """
    Identify this browser using a random, HttpOnly cookie.

    This separates browser histories but is not a replacement
    for proper user authentication.
    """
    owner_id = request.cookies.get(OWNER_COOKIE, "")

    if re.fullmatch(r"[A-Za-z0-9_-]{32,100}", owner_id):
        return owner_id, False

    return secrets.token_urlsafe(32), True


def set_owner_cookie(
    response: Response,
    request: Request,
    owner_id: str,
    is_new: bool,
) -> None:
    if not is_new:
        return

    response.set_cookie(
        key=OWNER_COOKIE,
        value=owner_id,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
        path="/",
    )


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": settings.app_name,
    }


# ---------------------------------------------------------
# Conversation history
# ---------------------------------------------------------


@router.get("/conversations")
def conversations(
    request: Request,
    response: Response,
):
    owner_id, is_new = resolve_owner(request)
    set_owner_cookie(response, request, owner_id, is_new)

    return {"conversations": list_conversations(owner_id)}


@router.post("/conversations", status_code=201)
def new_conversation(
    request: Request,
    response: Response,
):
    owner_id, is_new = resolve_owner(request)
    set_owner_cookie(response, request, owner_id, is_new)

    conversation = create_conversation(owner_id)

    return {
        "conversation": {
            key: value for key, value in conversation.items() if key != "owner_id"
        }
    }


@router.get("/conversations/{conversation_id}")
def open_conversation(
    conversation_id: str,
    request: Request,
    response: Response,
):
    owner_id, is_new = resolve_owner(request)
    set_owner_cookie(response, request, owner_id, is_new)

    conversation = get_conversation(
        owner_id,
        conversation_id,
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {
        "conversation": conversation,
        "messages": get_messages(
            owner_id,
            conversation_id,
        ),
    }


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
    request: Request,
    response: Response,
):
    owner_id, is_new = resolve_owner(request)
    set_owner_cookie(response, request, owner_id, is_new)

    try:
        updated = rename_conversation(
            owner_id,
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
            owner_id,
            conversation_id,
        )
    }


@router.delete("/conversations/{conversation_id}")
def remove_conversation(
    conversation_id: str,
    request: Request,
    response: Response,
):
    owner_id, is_new = resolve_owner(request)
    set_owner_cookie(response, request, owner_id, is_new)

    deleted = delete_conversation(
        owner_id,
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
# Chat
# ---------------------------------------------------------


@router.post("/chat")
def chat(
    payload: ChatRequest,
    request: Request,
):
    owner_id, is_new = resolve_owner(request)
    conversation_id = payload.conversation_id

    # Verify ownership before starting the streamed response.
    if conversation_id:
        conversation = get_conversation(
            owner_id,
            conversation_id,
        )

        if conversation is None:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            )

    def generate():
        try:
            # Save the user's question before running the agent.
            if conversation_id:
                save_message(
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=payload.question,
                )

            initial = {
                "question": payload.question,
                "current_query": payload.question,
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

            # Save the assistant response to the conversation.
            if conversation_id:
                save_message(
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer,
                    source_used=source_used,
                    citations=citations,
                    trace=trace_items,
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
            logger.exception("Chat request failed")

            yield json.dumps(
                {
                    "type": "error",
                    "detail": (
                        "The agent could not complete this request. "
                        "Please try again."
                    ),
                }
            ) + "\n"

    stream = StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )

    set_owner_cookie(stream, request, owner_id, is_new)

    return stream


# ---------------------------------------------------------
# HR document uploads
# ---------------------------------------------------------


@router.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    x_admin_key: str = Header(default=""),
):
    configured_key = settings.admin_api_key or ""

    if not configured_key or configured_key == DEFAULT_ADMIN_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "Document uploads are not configured. " "Set a private ADMIN_API_KEY."
            ),
        )

    if not hmac.compare_digest(
        x_admin_key,
        configured_key,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin key",
        )

    original_name = Path((file.filename or "document").replace("\\", "/")).name

    suffix = Path(original_name).suffix.lower()

    if suffix not in SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=("Supported file types: " f"{', '.join(sorted(SUPPORTED))}"),
        )

    stem = (
        re.sub(
            r"[^\w.-]+",
            "_",
            Path(original_name).stem,
        ).strip(
            "._"
        )[:100]
        or "document"
    )

    safe_filename = f"{stem}{suffix}"

    upload_root = Path(settings.upload_dir).resolve()
    upload_folder = upload_root / uuid.uuid4().hex
    destination = upload_folder / safe_filename

    try:
        upload_folder.mkdir(
            parents=True,
            exist_ok=False,
        )

        written = 0

        with destination.open("wb") as output:
            while True:
                chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)

                if not chunk:
                    break

                written += len(chunk)

                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=("The maximum document upload size " "is 10 MB."),
                    )

                output.write(chunk)

        if written == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is empty.",
            )

    except HTTPException:
        shutil.rmtree(
            upload_folder,
            ignore_errors=True,
        )
        raise

    except Exception:
        shutil.rmtree(
            upload_folder,
            ignore_errors=True,
        )

        logger.exception("Failed to save uploaded document")

        raise HTTPException(
            status_code=500,
            detail="Could not save the uploaded document.",
        ) from None

    finally:
        await file.close()

    try:
        documents = load_file(destination)
        chunks = chunk_documents(documents)

        if not chunks:
            shutil.rmtree(
                upload_folder,
                ignore_errors=True,
            )

            raise HTTPException(
                status_code=400,
                detail=("No readable text was found in this document."),
            )

    except HTTPException:
        raise

    except Exception:
        shutil.rmtree(
            upload_folder,
            ignore_errors=True,
        )

        logger.exception(
            "Failed to read uploaded document: %s",
            safe_filename,
        )

        raise HTTPException(
            status_code=400,
            detail=("Could not read this file. Upload a valid " "supported document."),
        ) from None

    try:
        ids = add_documents(chunks)

    except Exception:
        logger.exception(
            "Failed to index uploaded document: %s",
            safe_filename,
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "The document was uploaded but could not be indexed. "
                "Please try again later."
            ),
        ) from None

    return {
        "message": "Document indexed",
        "file": safe_filename,
        "chunks": len(chunks),
        "ids_created": len(ids),
    }

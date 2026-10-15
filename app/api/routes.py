import hmac
import json
import logging
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.rag.vectorstore import add_documents
from app.rag.workflow import agent_graph
from app.services.audit import write_audit
from app.services.ingestion import SUPPORTED, chunk_documents, load_file

router = APIRouter(prefix="/api")
settings = get_settings()
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_READ_CHUNK_BYTES = 64 * 1024
DEFAULT_ADMIN_KEY = "change-me-in-production"


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": settings.app_name,
    }


@router.post("/chat")
def chat(payload: ChatRequest):
    def generate():
        try:
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

            # Audit failures should not discard an otherwise valid answer.
            try:
                write_audit(
                    payload.question,
                    final_state.get("source_used", ""),
                    final_state.get("trace", []),
                )
            except Exception:
                logger.exception("Failed to write chat audit record")

            yield json.dumps(
                {
                    "type": "final",
                    "answer": final_state.get("answer", ""),
                    "source_used": final_state.get("source_used", ""),
                    "trace": final_state.get("trace", []),
                    "citations": final_state.get("citations", []),
                    "rewritten_query": final_state.get(
                        "current_query",
                        payload.question,
                    ),
                }
            ) + "\n"

        except Exception:
            # Keep internal exception details out of the browser response.
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


@router.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    x_admin_key: str = Header(default=""),
):
    # Never allow the documented placeholder key in production.
    configured_key = settings.admin_api_key or ""

    if not configured_key or configured_key == DEFAULT_ADMIN_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "Document uploads are not configured. "
                "Set a private ADMIN_API_KEY in your .env file."
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

    # Normalize Windows-style paths before extracting the filename.
    original_name = Path((file.filename or "document").replace("\\", "/")).name

    suffix = Path(original_name).suffix.lower()

    if suffix not in SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=("Supported file types: " f"{', '.join(sorted(SUPPORTED))}"),
        )

    # Sanitize the filename and keep the approved extension.
    stem = re.sub(
        r"[^\w.-]+",
        "_",
        Path(original_name).stem,
        flags=re.UNICODE,
    )
    stem = stem.strip("._")[:100] or "document"
    safe_filename = f"{stem}{suffix}"

    upload_root = Path(settings.upload_dir).resolve()
    upload_folder = upload_root / uuid.uuid4().hex
    destination = upload_folder / safe_filename

    # Stream the upload to disk instead of reading it all into memory.
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
            detail=("Could not save the uploaded document. " "Please try again."),
        ) from None

    finally:
        await file.close()

    # Parse and split the saved file into searchable chunks.
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
            detail=(
                "Could not read this file. Upload a valid PDF, "
                "DOCX, TXT, or MD document."
            ),
        ) from None

    # Index the chunks in the existing Pinecone namespace.
    try:
        ids = add_documents(chunks)

    except Exception:
        # Keep the saved file because indexing may have partially
        # succeeded. Detailed errors remain in the server logs.
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

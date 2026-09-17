import json
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


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)


@router.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}


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
            print("CHAT: before agent_graph.stream()", flush=True)
            for update in agent_graph.stream(initial, stream_mode="updates"):
                print(f"CHAT: received update → {update}", flush=True)
                for node_name, node_update in update.items():
                    final_state.update(node_update)

                    yield (
                        json.dumps(
                            {
                                "type": "trace",
                                "node": node_name,
                            }
                        )
                        + "\n"
                    )

            write_audit(
                payload.question,
                final_state.get("source_used", ""),
                final_state.get("trace", []),
            )

            yield (
                json.dumps(
                    {
                        "type": "final",
                        "answer": final_state.get("answer", ""),
                        "source_used": final_state.get("source_used", ""),
                        "trace": final_state.get("trace", []),
                        "citations": final_state.get("citations", []),
                        "rewritten_query": final_state.get(
                            "current_query", payload.question
                        ),
                    }
                )
                + "\n"
            )

        except Exception as exc:
            yield json.dumps(
                {
                    "type": "error",
                    "detail": str(exc),
                }
            ) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
    )


@router.post("/ingest")
async def ingest(file: UploadFile = File(...), x_admin_key: str = Header(default="")):
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(
            status_code=400, detail=f"Supported: {', '.join(sorted(SUPPORTED))}"
        )
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(file.filename).name
    dest.write_bytes(await file.read())
    docs = load_file(dest)
    chunks = chunk_documents(docs)
    ids = add_documents(chunks)
    return {
        "message": "Document indexed",
        "file": dest.name,
        "chunks": len(chunks),
        "ids_created": len(ids),
    }

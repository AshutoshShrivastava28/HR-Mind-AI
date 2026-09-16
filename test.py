from app.services.ingestion import load_file, chunk_documents
from pathlib import Path

docs = load_file(Path("data/sample_kb/hr_operations_runbook.md"))
chunked_docs = chunk_documents(docs)

print(len(chunked_docs))

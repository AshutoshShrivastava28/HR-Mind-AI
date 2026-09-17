# HR Mind AI

An end-to-end Agentic RAG HR knowledge copilot that turns company HR policies and employee-support workflows into a deployable AI application using LangGraph, FastAPI, Pinecone, Groq, Tavily, FastEmbed, Docker, and JavaScript.

## 🚀 Live Demo

**Live Application:** http://168.144.190.198:8000

> The application is deployed as a Docker container on a DigitalOcean Droplet.

---

## 1. Business Problem

### Customer

XYZ Company, a fictional 5,000-employee retail company.

### Problem

The HR team maintains many internal documents: leave policies, remote-work rules, payroll guidance, benefits information, onboarding procedures, conduct policies, and HR operations runbooks.

Employees still ask repetitive HR questions because they may not know where the correct policy lives, keyword search can return too many documents, generic chatbots can invent policy details, internal documents may not contain current public information, and some questions require fresh external information.

### Example

An employee asks:

> “How many annual leave days do employees receive?”

The answer exists in the private company HR knowledge base, so the system should answer from internal policy without searching the public internet.

Another employee asks:

> “What are the latest public holiday rules in Bangladesh?”

The internal knowledge base may not contain current public information. The system can recognize insufficient private evidence, use external search, evaluate the evidence, and clearly identify the information as external and requiring HR validation.

### Business Goal

Build an HR Policy Copilot that:

1. Searches trusted private HR knowledge first.
2. Checks whether retrieved evidence is sufficient.
3. Uses web search only when private knowledge is insufficient.
4. Rewrites weak queries and retries when necessary.
5. Generates grounded answers.
6. Shows the LangGraph decision path for transparency and debugging.
7. Allows authorized HR staff to add new company documents.

---

## 2. Why This Is an FDE-Style Project

A Forward Deployed Engineer does more than build an LLM notebook. The role connects a customer problem to a usable production-oriented solution:

```text
Customer Problem
      ↓
Discovery & Requirements
      ↓
Solution Architecture
      ↓
Data / Knowledge Integration
      ↓
Agentic RAG Development
      ↓
API Development
      ↓
User Interface
      ↓
Security + Audit + Testing
      ↓
Deployment
      ↓
Observe + Improve
```

---

## 3. Architecture

<img width="1536" height="1024" alt="Architecture" src="https://github.com/user-attachments/assets/6c9373f6-5354-4bfd-9cdd-bf5cb82ccbc3" />

```text
Employee / HR User
        ↓
HTML / CSS / JavaScript Web UI
        ↓ POST /api/chat
FastAPI
        ↓
LangGraph Agentic RAG Controller
        ↓
 ┌──────────────────┐
 ↓                  ↓
Private HR KB       Tavily Web Search
Pinecone            (fallback only)
 └─────────┬────────┘
           ↓
Groq LLM
Grounded Answer
```

### Deployment Architecture

```text
GitHub
   ↓
DigitalOcean Droplet
   ↓
Docker Container
   ↓
FastAPI + LangGraph
   ↓
Groq + Pinecone + Tavily
```

---

## 4. Agentic RAG Workflow

```text
Question
   ↓
[1] Route Question
   ├── Greeting / simple chat ─────────→ Direct Answer
   │
   └── HR / policy question
                ↓
[2] Retrieve from Private Pinecone KB
                ↓
[3] Grade Private Evidence
       ┌────────┴────────┐
       │                 │
     GOOD               WEAK
       │                 │
       ▼                 ▼
Generate from KB   [4] Tavily Web Search
                         ↓
                  [5] Grade Web Evidence
                    ┌────┴─────┐
                    │          │
                  GOOD        WEAK
                    │          │
                    ▼          ▼
              Generate Web  [6] Rewrite Query
                               ↓
                         Retry Private KB
                               ↓
                        Max retry reached?
                               ↓
                    Insufficient Evidence
```

This workflow is designed to prefer private company knowledge and use external web information only when the private evidence is insufficient.

---

## 5. Technology Stack

| Layer           | Technology                     | Purpose                                                           |
| --------------- | ------------------------------ | ----------------------------------------------------------------- |
| Agent workflow  | LangGraph                      | Stateful routing and conditional decisions                        |
| LLM             | Groq (`openai/gpt-oss-120b`)   | Routing, evidence grading, query rewriting, and answer generation |
| Embeddings      | FastEmbed (`all-MiniLM-L6-v2`) | Lightweight local text embeddings                                 |
| Vector DB       | Pinecone                       | Persistent HR knowledge base and similarity search                |
| External search | Tavily                         | Web fallback when private HR evidence is insufficient             |
| API             | FastAPI                        | Backend API and application server                                |
| Frontend        | HTML / CSS / JavaScript        | Employee-facing web interface                                     |
| Audit           | SQLite                         | Decision-path logging                                             |
| Packaging       | Docker                         | Reproducible application packaging                                |
| Deployment      | DigitalOcean Droplet           | Hosting the Dockerized application                                |

---

## 6. Project Structure

```text
HR-Mind-AI/
├── app/
│   ├── api/
│   │   └── routes.py
│   ├── core/
│   │   ├── config.py
│   │   └── logging.py
│   ├── rag/
│   │   ├── state.py
│   │   ├── vectorstore.py
│   │   └── workflow.py
│   ├── services/
│   │   ├── audit.py
│   │   └── ingestion.py
│   └── main.py
├── data/
│   └── sample_kb/
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js
├── templates/
│   └── index.html
├── uploads/
├── Dockerfile
├── ingest_sample_kb.py
├── requirements.txt
├── run.py
└── README.md
```

---

## 7. Local Setup

### Step 1 — Create and activate a virtual environment

```bash
python -m venv .venv
```

```powershell
.venv\Scripts\activate
```

### Step 2 — Install dependencies

```bash
python -m pip install -r requirements.txt
```

### Step 3 — Configure environment variables

Create a `.env` file and add your API keys and configuration.

```env
GROQ_API_KEY=your_groq_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here
PINECONE_API_KEY=your_pinecone_api_key_here

PINECONE_INDEX_NAME=fde-hr-policy-rag
PINECONE_NAMESPACE=company-hr-kb

EMBEDDING_MODEL=all-minilm-l6-v2

TOP_K=4
MAX_RETRIES=1

ADMIN_API_KEY=change-me-in-production
APP_ENV=development
```

### Step 4 — Load sample HR knowledge

```bash
python ingest_sample_kb.py
```

### Step 5 — Run the application

```bash
python run.py
```

Open:

```text
http://127.0.0.1:8000
```

FastAPI documentation:

```text
http://127.0.0.1:8000/docs
```

---

## 8. Docker

Build the image:

```bash
docker build -t hr-mind-ai .
```

Run the application:

```bash
docker run -d --restart unless-stopped \
  --name hr-mind-ai \
  -p 8000:10000 \
  --env-file .env \
  hr-mind-ai
```

The container listens internally on port `10000` and is exposed on port `8000` on the host.

---

## 9. Deployment

The application is deployed on a DigitalOcean Droplet using Docker.

### Deployment flow

```text
GitHub Repository
       ↓
DigitalOcean Droplet
       ↓
Docker Image
       ↓
Docker Container
       ↓
FastAPI / LangGraph
       ↓
Groq + Pinecone + Tavily
```

### Live Application

**http://168.144.190.198:8000**

The Docker container is configured to restart automatically using:

```text
--restart unless-stopped
```

This allows the application to continue running when the local development machine is offline or disconnected.

---

## 10. Document Ingestion

Authorized HR users can upload PDF, TXT, Markdown, or DOCX documents from the web interface.

The ingestion flow is:

```text
HR Document
    ↓
Upload
    ↓
Text Extraction
    ↓
Chunking
    ↓
FastEmbed Embeddings
    ↓
Pinecone
    ↓
Persistent HR Knowledge
```

The original uploaded file is used as the ingestion input. The searchable knowledge used by the RAG system is stored in Pinecone.

---

## 11. Classroom / Portfolio Demo Scenarios

### Demo A — Private KB Success

Ask:

> **How many annual leave days do employees receive?**

Expected path:

```text
Router → KB
Private KB Retrieval
KB Grade → GOOD
Generate from Private KB
```

### Demo B — Company Policy Question

Ask:

> **How many days per week can I work remotely?**

Expected result:

```text
Router
→ Private KB Retrieval
→ Good Evidence
→ Answer from HR Policy
```

No web search should be required when the company knowledge base already contains sufficient evidence.

### Demo C — External / Current Information

Ask:

> **What are the latest public holiday rules in Italy?**

Expected path when the internal HR documents are insufficient:

```text
Router → KB
Private KB Retrieval
KB Grade → WEAK
Tavily Search
Web Grade → GOOD
Generate Web Answer
```

### Demo D — Weak Query Rewrite

Ask an ambiguous question such as:

> **What happens if mine is wrong?**

If neither private nor web evidence is sufficient, the workflow can rewrite the query, retry the private knowledge base, and eventually return an insufficient-evidence response rather than fabricating a policy answer.

---

## 12. Observability

The application exposes a live **Agent Trace** in the frontend.

Example:

```text
Route question
      ↓
Retrieve private KB
      ↓
Grade evidence
      ↓
Web fallback / query rewrite
      ↓
Generate grounded answer
```

The trace makes LangGraph decisions visible during both debugging and demonstrations.

---

## 13. Key Features

- Agentic RAG with conditional LangGraph routing
- Private knowledge-first retrieval
- Evidence grading before answer generation
- Tavily web fallback
- Query rewriting and retry logic
- Grounded answer generation
- Pinecone vector search
- FastEmbed local embeddings
- HR document ingestion
- FastAPI backend
- Live streaming Agent Trace
- Source and citation display
- Dockerized deployment
- DigitalOcean hosting
- Responsive dark enterprise-style frontend
- SQLite audit logging

---

This project applies an Agentic RAG reference architecture to an HR employee-support use case.

## 14. Future Improvements

- Authentication and role-based HR access
- Persistent object storage for original uploaded documents
- HTTPS and a custom domain
- Better document versioning and deletion
- More detailed audit dashboards
- Production monitoring and alerting

---

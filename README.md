# Maya — Autism Hounslow Information Assistant

Maya is a free web-based information assistant for autistic people and carers in Hounslow, powered by a Retrieval-Augmented Generation (RAG) system built with FastAPI and ChromaDB.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Environment variables](#2-environment-variables)
3. [Running locally](#3-running-locally)
4. [Re-indexing the knowledge base](#4-re-indexing-the-knowledge-base)
5. [Running tests](#5-running-tests)
6. [Deploying on Replit (Autoscale)](#6-deploying-on-replit-autoscale)
7. [Custom subdomain setup](#7-custom-subdomain-setup)
8. [Wix integration instructions](#8-wix-integration-instructions)

---

## 1. Prerequisites

- Python 3.11+
- pip

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## 2. Environment variables

Set these in your shell, `.env` file, or (on Replit) via the **Secrets** tab.

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | Yes* | — | Groq API key (used if `LLM_API_KEY` is not set) |
| `LLM_PROVIDER` | No | auto | Informational label only — provider is inferred from keys |
| `LLM_BASE_URL` | No | — | Base URL for OpenAI-compatible endpoint (e.g. `http://localhost:11434/v1` for Ollama) |
| `LLM_API_KEY` | No | — | API key for custom endpoint. Overrides `GROQ_API_KEY` |
| `LLM_MODEL` | No | `llama-3.3-70b-versatile` (Groq) or `qwen2.5:72b` (Ollama) | Model name |
| `TEMPERATURE` | No | `0` | LLM temperature (0 = deterministic) |
| `TOP_P` | No | `1.0` | LLM top-p sampling |
| `EMBEDDING_MODEL` | No | `all-MiniLM-L6-v2` | SentenceTransformers embedding model |
| `HF_TOKEN` | No | — | Hugging Face token (for private embedding models) |
| `OPENAI_API_KEY` | No | — | OpenAI API key (if using OpenAI as LLM backend) |

### Provider examples

**Groq (default — fast, free tier available):**
```
GROQ_API_KEY=gsk_...
LLM_MODEL=llama-3.3-70b-versatile
TEMPERATURE=0
```

**Ollama running locally with Qwen:**
```
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:72b
TEMPERATURE=0
```

**OpenAI GPT-4o:**
```
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o
TEMPERATURE=0
```

---

## 3. Running locally

```bash
# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload

# Visit
open http://localhost:5000
```

The server starts immediately. The RAG knowledge base loads in the background (~30–60 seconds on first run, ~10 seconds on subsequent runs once the ChromaDB database is cached).

---

## 4. Re-indexing the knowledge base

Run this whenever the seed JSONL file is updated:

```bash
python scripts/reindex.py
```

This clears the existing ChromaDB collection and rebuilds it from:
- `data/maya_hounslow_knowledge_seed.jsonl` (primary seed)
- Any other `.jsonl` or `.csv` files in `data/`

Options:

```bash
# Use a different seed file
python scripts/reindex.py --seed-file data/my_custom_data.jsonl

# Add to existing collection without clearing it first
python scripts/reindex.py --no-reset
```

### Adding new knowledge entries

Edit `data/maya_hounslow_knowledge_seed.jsonl`. Each line is a JSON object. Required fields:

```json
{
  "id": "unique_id",
  "title": "Entry title",
  "url": "https://source.url",
  "source_name": "Gov.UK",
  "source_type": "government",
  "category": "benefits",
  "subcategory": "PIP",
  "audience": ["parent_carer"],
  "age_range": "all_ages",
  "locality": "national_uk",
  "description_plain": "Plain English description of this entry.",
  "format_type": "guide",
  "reliability_score": 5,
  "steps_summary": "1. First step\n2. Second step",
  "eligibility_summary": "Who qualifies...",
  "evidence_required": "Documents needed...",
  "contacts": {"phone": "0800 123 4567"}
}
```

After editing, run `python scripts/reindex.py` to rebuild the database.

---

## 5. Running tests

```bash
pytest tests/ -v
```

---

## 6. Deploying on Replit (Autoscale)

1. Open your Repl → **Deploy** tab → choose **Autoscale**.
2. Set the **Run command** to:
   ```
   uvicorn app.main:app --host 0.0.0.0 --port 5000
   ```
3. Set **Health check path** to `/health`.
4. In the **Secrets** tab, add all required environment variables (see §2 above).
5. Click **Deploy**. Your app will be available at a `.replit.app` URL.

> **Note:** The ChromaDB vector database is stored in `./chroma_db/`. On Replit Autoscale, this directory persists between requests within a running instance but is rebuilt when the instance restarts. The knowledge base re-indexes automatically from the seed file on each cold start (via `scripts/reindex.py` or the background initialisation).

---

## 7. Custom subdomain setup

To serve Maya at `maya.autismhounslow.org.uk`:

### Option A — Replit custom domain (recommended)

1. In the Replit **Deploy** settings → **Custom domains** → Add `maya.autismhounslow.org.uk`.
2. Replit will show you a CNAME record to add. It looks like:
   ```
   CNAME  maya  <your-repl>.replit.app
   ```
3. Log in to your DNS provider (e.g. Cloudflare, 123-reg, GoDaddy) and add the CNAME record.
4. Wait for DNS propagation (5 minutes to 48 hours).
5. Replit automatically provisions HTTPS/TLS.

### Option B — Reverse proxy (nginx or Cloudflare Workers)

If you run Maya on your own server:

```nginx
server {
    listen 443 ssl;
    server_name maya.autismhounslow.org.uk;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
```

---

## 8. Wix integration instructions

There are two ways to embed Maya on a Wix website.

---

### Option 1 — Link button (simplest)

Add a button on any Wix page that opens Maya in a new tab:

1. In the Wix editor, add a **Button** element.
2. Set the button text to "Ask Maya" or "Get information".
3. Under **Link** → choose **Web address** → enter your Maya URL (e.g. `https://maya.autismhounslow.org.uk`).
4. Tick **"Open in new tab"**.

This is the recommended approach for most visitors — it gives Maya full screen space and works on all devices.

---

### Option 2 — Embedded iframe

You can embed Maya directly on a Wix page using Wix's **HTML iframe** element.

**Steps:**

1. In the Wix editor, click **Add** → **Embed** → **Embed a Widget** (or **Custom Embeds** → **Embed HTML**).
2. In the HTML code box, paste:

```html
<iframe
  src="https://maya.autismhounslow.org.uk"
  width="100%"
  height="700"
  style="border:none; border-radius:12px;"
  title="Maya – Autism Hounslow Information Assistant"
  allow="clipboard-write"
></iframe>
```

3. Resize the element to fill the content area of your page.
4. Publish the page.

**Important notes for iframe embedding:**
- Maya must be served over HTTPS (it is, on Replit or with a valid TLS certificate).
- Set `height` to at least `650px` for usable chat space on desktop; `500px` minimum on mobile.
- Wix's iframe element does not support full-page height (`100vh`) — use a fixed pixel height or a percentage of the viewport using Wix's own resizing handles.
- The `top-banner` disclaimer is visible inside the iframe, satisfying the accessibility requirement.

---

### Recommended page structure on Wix

```
Page: "Get Information" or "Maya Assistant"
├── Heading: "Ask Maya – Autism Hounslow Information Assistant"
├── Paragraph: "Maya provides general information about autism support, benefits,
│              and services in Hounslow and the UK. Maya does not give medical
│              or legal advice."
└── HTML iframe embed → Maya URL
```

---

## Architecture overview

| Component | Technology |
|---|---|
| Web framework | FastAPI + Uvicorn |
| Vector database | ChromaDB (persisted to `./chroma_db/`) |
| Embeddings | SentenceTransformers (`all-MiniLM-L6-v2` by default) |
| LLM | Configurable via env vars (Groq/Llama default) |
| Frontend | Vanilla HTML + CSS + JavaScript |
| Knowledge base | JSONL seed file + optional web crawl |

### Key endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Main chat interface |
| `/health` | GET | Health check (for deployment probes) |
| `/status` | GET | RAG readiness status |
| `/chat` | POST | Answer a question |
| `/feedback` | POST | Submit a feedback report |

---

## Privacy

Maya does not store question text, IP addresses, or personal information.
See the **Privacy notice** link inside the chat interface for details.

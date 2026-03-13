# Maya RAG System — Technical Guide

How the Retrieval-Augmented Generation (RAG) model behind Maya was built.

---

## What is RAG and Why We Used It

A standard AI chatbot answers questions purely from what it was trained on, which may be outdated, inaccurate, or not UK-specific. RAG (Retrieval-Augmented Generation) fixes this by splitting the problem into two steps:

1. **Retrieve** — search a curated knowledge base for the most relevant information
2. **Generate** — pass that information to an AI language model to write a clear, cited answer

This means Maya's answers are always grounded in real, current documents from trusted UK sources, rather than hallucinated or generic content.

---

## System Overview

```
User Question
      │
      ▼
┌─────────────┐
│  Guardrails │  ← blocks clinical/legal/crisis requests immediately
└─────────────┘
      │
      ▼
┌─────────────┐
│   Content   │  ← LLM checks whether the question is autism-related
│  Moderator  │
└─────────────┘
      │
      ▼
┌─────────────┐
│    Query    │  ← LLM rewrites the question to improve search accuracy
│  Enhancer   │
└─────────────┘
      │
      ▼
┌─────────────┐
│   Vector    │  ← searches ChromaDB using semantic similarity + authority ranking
│   Search    │
└─────────────┘
      │
      ▼
┌─────────────┐
│  Response   │  ← Llama 3.3 70B writes the answer using retrieved chunks
│  Generator  │
└─────────────┘
      │
      ▼
Cited Answer with Sources
```

---

## Component 1: Knowledge Base (What Maya Knows)

Maya's knowledge comes from two distinct sources that are combined into one searchable database.

### Source A — Web Crawling

At startup, Maya's crawler visits a curated list of trusted UK websites and extracts clean article text. The crawler uses **Trafilatura** (a specialist content extractor) to strip out navigation menus, adverts, and footers, leaving only meaningful content.

**Trusted sources crawled:**

| Source | Authority Level | What it covers |
|--------|----------------|----------------|
| Gov.UK | Highest (Government) | SEND guidance, DLA, Universal Credit, EHC plans |
| NHS | Very High | Autism diagnosis, signs, assessments, co-occurring conditions |
| National Autistic Society | High (National Charity) | Autism support, employment, mental health, strategies |
| Ambitious About Autism | High (National Charity) | Education, young people |
| IPSEA | High (Specialist Legal Org) | SEND tribunal appeals, EHC plan legal advice |
| Hounslow Council | High (Local Authority) | Local SEND services, Local Offer, EHC plans in Hounslow |

Hounslow Council pages are tagged as `location_specific = True` so Maya can prioritise them when a user asks about local services.

**Chunking:** Each crawled page is split into overlapping chunks of ~1,000 words with a 200-word overlap between chunks. This overlap prevents important information being cut in half at a chunk boundary.

### Source B — Structured Knowledge Dataset

In addition to web crawling, Maya has a hand-curated dataset of 28 detailed bureaucratic guides stored in a JSONL file (`data/maya_hounslow_knowledge_seed.jsonl`). These entries were written specifically for Maya and cover step-by-step processes that are hard to extract reliably from web pages.

**Coverage of the structured dataset:**

| Category | Entries | Examples |
|----------|---------|---------|
| Benefits | 6 | DLA application, Carer's Allowance, Universal Credit LCWRA, PIP, Access to Work |
| Education / SEND | 5 | EHC plan requests, SEND tribunal appeals, school support |
| Adult Social Care | 4 | Care Act assessments, direct payments, support planning |
| Appeals | 4 | How to challenge decisions at tribunal |
| Health | 2 | NHS autism pathways, Talking Therapies |
| Employment | 1 | Access to Work grant |
| Travel | 3 | TfL travel training, Blue Badge, Dial-a-Ride |
| Community | 3 | Hounslow-specific local groups and contacts |

Each structured entry contains:
- Step-by-step instructions
- Eligibility criteria
- Required evidence
- Important deadlines and dates
- Contact details (phone, email, address, opening hours)
- Legal basis (e.g. Children and Families Act 2014)
- Notes specifically written for Maya's use

Structured entries receive a **relevance boost of +0.15** during search, so Maya prefers them over general web content when both are available.

---

## Component 2: Vector Store (How Search Works)

All content — both crawled pages and structured entries — is converted into **vector embeddings** and stored in a **ChromaDB** database on disk.

### Embeddings

A vector embedding turns a piece of text into a list of numbers (a vector) that captures its meaning. Two pieces of text about similar topics will have vectors that are mathematically close together.

Maya uses **`all-MiniLM-L6-v2`** from the SentenceTransformers library (HuggingFace). This is a compact, fast model well-suited for semantic search — it produces 384-dimensional vectors.

**Why this model?**
- Optimised for semantic similarity tasks
- Runs efficiently on CPU (no GPU needed)
- Free and open-source
- Fast enough to embed queries in real time (~30ms)

### Authority-Based Reranking

Raw vector similarity scores are adjusted by source authority before returning results. This ensures a Government or NHS source is preferred over a less authoritative source when both are equally relevant:

| Authority Level | Score Boost |
|----------------|------------|
| Government (Gov.UK) | +0.5 |
| NHS | +0.4 |
| National Charity | +0.3 |
| Local Authority | +0.2 |
| Specialist Org | +0.1 |

### Relevance Filtering

Results below a relevance threshold (distance > 0.8) are filtered out. If fewer than 3 results pass the threshold, the top 6 are kept regardless, to ensure Maya always has something to work with.

The final relevance score for each chunk is calculated from:
- Vector similarity distance
- Source authority boost
- Structured entry boost (+0.15)
- Content length (longer content gets a small boost)
- Exact term matches in the title (+0.05 per term) and body (+0.02 per term)

---

## Component 3: Query Enhancement

Before searching, Maya rewrites the user's question using the Groq LLM. This dramatically improves search accuracy.

**Example:**

> User types: `"What is autism?"`
>
> Enhanced query: `"What is autism spectrum disorder and how is it diagnosed and supported in the UK, including through NHS services and local authority provisions?"`

The enhanced query is richer in UK-specific terminology, which finds better matches in the knowledge base.

If Hounslow-specific terms appear in the question (`"hounslow"`, `"local"`, `"near me"`, `"tw3"`, `"tw4"`, etc.), Maya runs a separate Hounslow-only search first and combines the results with general results.

---

## Component 4: Language Model (Response Generation)

Maya uses **Llama 3.3 70B Versatile** via the **Groq API** to write answers.

**Why Groq?**
- Extremely fast inference (~1-2 seconds for a full response)
- Free tier available for development
- Runs open-source models (Llama), not proprietary

**Generation settings:**
- Temperature: `0.3` — low, to keep answers factual and consistent
- Max tokens: `1,000` — enough for a detailed answer without being overwhelming

The LLM receives a system prompt that tells it:
- It is Maya, a UK autism assistant
- It must use only the provided context to answer
- It must cite sources inline (e.g. `[NHS]`, `[National Autistic Society]`)
- It must never provide medical diagnoses, treatment plans, or specific legal advice
- If someone is in crisis, it must direct them to emergency services

---

## Component 5: Reading Comprehension Levels

Maya adjusts the complexity of its language based on the user's selected reading level. This was designed for autistic users who may have learning disabilities.

| Level | Target Audience | Rules |
|-------|----------------|-------|
| Clear | Ages 8-10 / Learning disabilities | Max 4-7 words per sentence, simple everyday words only, no acronyms, heavy use of bullet points |
| Standard | Ages 14-18 / General public | Accessible language, technical terms explained, balanced detail |
| Complex | Adults / Professionals | Full technical language, legal/medical terminology, comprehensive detail |

The language guidelines are injected directly into the LLM system prompt, with mandatory word replacement rules at the Clear level (e.g. "help" not "assistance", "doctor" not "GP", "get" not "obtain").

---

## Component 6: Safety Guardrails

Before any question reaches the AI, it passes through a regex-based guardrail system that catches three categories of high-risk requests:

### Clinical Guardrail
Triggered by patterns like: `"dose/dosage"`, `"start/stop medication"`, `"diagnosis"`, `"treatment plan"`

Response: Directs user to their GP or NHS 111. If emergency, to call 999.

### Legal Guardrail
Triggered by patterns like: `"legal advice"`, `"can I sue"`, `"guarantee winning tribunal"`

Response: Directs user to IPSEA, Citizens Advice, or a qualified solicitor.

### Crisis Guardrail
Triggered by patterns like: `"self-harm"`, `"suicidal"`, `"can't cope"`, `"want to die"`

Response: Immediately directs to 999, A&E, or Samaritans (116 123, free 24/7).

These guardrails run **before** the LLM is ever called, so they are fast, predictable, and cannot be bypassed by clever phrasing that might fool the AI.

A second layer of content checking uses the LLM itself to catch off-topic questions (e.g. requests for jokes, sports results, or unrelated medical advice) that the regex patterns might miss.

---

## Component 7: Server Architecture

### Background Initialization

Loading the ChromaDB database and SentenceTransformer model takes 8-15 seconds in development and up to 50 seconds on a cold deployment. To avoid the server failing health checks during this time, Maya uses an **async background task**:

1. Server starts in ~0.2 seconds
2. `/health` endpoint responds immediately — passes deployment health checks
3. RAG system loads in the background (non-blocking)
4. Once ready, all `/chat` requests use the full RAG pipeline
5. If a user sends a message before initialization completes, they receive a friendly "please wait" message

### API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /` | Serves the chat interface |
| `GET /health` | Ultra-fast health check for deployment (returns instantly) |
| `GET /status` | Returns whether RAG system is ready (`rag_ready: true/false`) |
| `GET /warmup` | Exercises all components to prevent cold starts |
| `POST /chat` | Main conversation endpoint — accepts question and comprehension level |

---

## Tech Stack Summary

| Component | Technology | Why |
|-----------|-----------|-----|
| Web framework | FastAPI (Python) | Fast, modern, automatic API docs |
| ASGI server | Uvicorn | Production-grade, async |
| Vector database | ChromaDB | Open-source, runs locally, persistent |
| Embeddings model | SentenceTransformers `all-MiniLM-L6-v2` | Fast, free, CPU-friendly |
| LLM API | Groq (Llama 3.3 70B) | Fast inference, free tier, open-source model |
| Web crawler | Trafilatura + BeautifulSoup4 | Accurate content extraction |
| Data validation | Pydantic | Request/response type safety |
| Frontend | Vanilla HTML / CSS / JavaScript | No framework dependency, fast load |
| Timezone | pytz (Europe/London) | UK-correct timestamps |

---

## Knowledge Base Statistics

At time of writing, the knowledge base contains **61 chunks** derived from:
- 28 hand-curated structured entries covering UK benefits, education, care, and employment
- Web-crawled content from 6 trusted UK sources across 30+ individual pages

---

## Limitations and Recommendations

**Content freshness:** Benefit rates, DLA/PIP amounts, and SEND deadlines change, typically at the April budget each year. The structured dataset should be reviewed and updated quarterly.

**UK-only:** Maya is designed exclusively for England (with a Hounslow focus). Scottish, Welsh, and Northern Irish users may receive information that does not apply to them, as devolved systems differ significantly.

**No memory:** Each question is answered independently. Maya does not remember previous messages in the same session or across sessions.

**Not a replacement for professionals:** Maya provides information, not advice. For individual cases, users should always be directed to GPs, IPSEA, Citizens Advice, or qualified solicitors.

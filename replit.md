# Overview

This is Maya, a UK-focused autism facts assistant built with FastAPI and vanilla JavaScript. The application provides a web-based chat interface for autistic people and carers to get information about UK autism support, services, benefits, and education. The system includes robust safety guardrails to prevent inappropriate medical, legal, or crisis advice.

# Recent Changes

**October 31, 2025**: Comprehensive structured knowledge dataset deployed
- **MAJOR MILESTONE**: Created and imported 28-entry structured knowledge seed dataset
- **COVERAGE**: Benefits (6), Education/SEND (5), Adult Social Care (4), Appeals (4), Health (2), Employment (1), Travel (3), Community (3)
- **QUALITY**: Each entry includes verified URLs, step-by-step guidance, deadlines, contacts, evidence requirements, legal basis
- **SOURCES**: Gov.UK, NHS, Hounslow Council, National Autistic Society, IPSEA, official charities
- **TESTING**: Confirmed accurate retrieval for DLA applications and SEND Tribunal appeals
- **IMPACT**: Users can now get detailed bureaucratic process guidance for UK autism support systems
- **NEXT STEPS**: Quarterly content reviews recommended to keep rates/deadlines current (especially April 2026 LCWRA change)

**October 30, 2025**: Fixed HuggingFace rate limiting and reverted to simple initialization
- **CRITICAL FIX**: Added HF_TOKEN secret to resolve "429 Too Many Requests" errors in deployment
- **ROOT CAUSE**: HuggingFace blocks downloads without authentication tokens
- **SOLUTION**: Free HuggingFace read token allows model downloads in production
- **DEPLOYMENT**: Reverted from async lazy initialization to simple blocking approach for reliability
- Simple synchronous initialization on first request (slow but reliable)
- Frontend shows helpful "Initializing..." message during first request (~1-2 min deployed)
- Health check endpoint responds instantly for deployment compatibility
- Answer delivered automatically after initialization completes
- Replaced "Tell me a joke" with "What is autism?" suggestion button
- Performance: Health check instant, first request 10-15s dev / 1-2min deployed, subsequent ~2s
- **LESSON LEARNED**: Always check deployment logs first when published site differs from dev

**September 2025**: Converted from OpenAI to Groq open-source LLMs
**January 2025**: Transformed from personal LLM app to specialized UK autism assistant
- Implemented comprehensive safety guardrails for clinical/legal/crisis questions
- Added UK-specific autism information system
- Created specialized chat interface with disclaimer banner
- Built complete test suite with 15 passing tests
- Configured for Europe/London timezone and en-GB locale

# User Preferences

Preferred communication style: Simple, everyday language
Target audience: UK autistic people and carers
Jurisdiction: United Kingdom (emphasis on England and Hounslow)

# Project Architecture

## Knowledge Base System

Maya uses **dual knowledge sources** for comprehensive coverage:

### Web Crawling System
- Crawls UK autism websites (NHS, NAS, Gov.UK, etc.)
- Extracts general autism information
- 14 documents from trusted sources
- Good for: Definitions, general guidance, factual information

### Structured Knowledge Import (NEW!)
- Imports curated JSONL/CSV files with rich metadata
- Contains step-by-step bureaucratic guides
- Includes contacts, deadlines, evidence requirements, legal basis
- Good for: PIP appeals, EHCP requests, Blue Badge applications, etc.
- **Prioritized in search** - Gets +0.15 relevance boost
- See `docs/STRUCTURED_KNOWLEDGE_GUIDE.md` for full details

## Backend Architecture
- **Framework**: FastAPI with Python 3.11, providing robust API endpoints
- **Core Endpoints**: 
  - `/` - Serves main chat interface
  - `/health` - Health check endpoint
  - `/chat` - Main conversation endpoint with safety guardrails
- **Safety System**: Regex-based guardrail patterns detecting clinical, legal, and crisis content
- **Configuration**: Environment variable-based with UK timezone and locale settings

## Frontend Architecture
- **Technology Stack**: Vanilla HTML, CSS, and JavaScript for simplicity
- **UI Design**: Dark theme with prominent safety disclaimer banner
- **Chat Interface**: MayaApp class managing real-time conversations
- **Responsive Design**: Mobile-friendly with accessible styling

## Safety & Compliance System
- **Guardrails**: Automatic detection and refusal of inappropriate content
- **Categories**: Clinical (diagnosis/medication), Legal (case-specific advice), Crisis (self-harm/suicide)
- **Refusal Templates**: Professional redirects to appropriate UK services (NHS 111, IPSEA, etc.)
- **Disclaimer**: Persistent banner emphasizing information-only nature

## Testing Architecture
- **Test Coverage**: 15 comprehensive tests covering endpoints, guardrails, and static assets
- **Policy Tests**: Parametrized tests ensuring safety guardrails work correctly
- **Smoke Tests**: Basic functionality verification for all endpoints
- **Static Asset Tests**: Verification that CSS, JavaScript, and HTML load correctly

# External Dependencies

## Core Dependencies
- **FastAPI**: Web framework for building the API backend
- **Uvicorn**: ASGI server for running the FastAPI application
- **Pydantic**: Data validation and API response modeling
- **pytz**: Timezone handling for Europe/London timestamps

## Development & Testing
- **pytest**: Test framework for comprehensive test suite
- **httpx**: HTTP client for API testing
- **python-dotenv**: Environment variable management

## Content Processing & RAG Implementation
- **ChromaDB**: Vector database for semantic search and document storage
- **SentenceTransformers**: Text embeddings for semantic similarity
- **Groq**: Open-source LLM API client for intelligent response generation
- **Trafilatura**: Web content extraction and text processing
- **BeautifulSoup4**: HTML parsing for web content extraction
- **lxml**: XML/HTML processing library
- **python-multipart**: Form data handling

# Current Status

**Phase 1 Complete**: Core application with safety guardrails
- ✅ FastAPI backend with UK autism focus
- ✅ Safety guardrails preventing inappropriate advice
- ✅ Professional UI with disclaimer
- ✅ Comprehensive test suite (15 tests passing)
- ✅ Static assets properly configured
- ✅ Workflow running on port 5000

**Phase 2 Complete**: Full RAG system implementation
- ✅ ChromaDB vector store with proper SentenceTransformer embeddings
- ✅ UK autism source crawling pipeline (14 documents from NHS, NAS, Gov.UK)
- ✅ Groq LLM integration with Llama 3.1 70B model (open-source via Groq API)
- ✅ Intelligent retrieval with authority ranking and Hounslow-specific routing
- ✅ Citation system with source transparency and disclaimers
- ✅ End-to-end RAG pipeline: crawl → embed → retrieve → synthesize → cite
- ✅ Production-ready system processing live chat requests with open-source models

# Technical Notes

- Application serves on port 5000 (production-optimized without --reload flag)
- Static files mounted at `/static/` route
- Europe/London timezone for all timestamps
- CORS enabled for development (should be restricted for production)
- Lazy loading RAG system with async background initialization for fast deployment
- First request triggers background initialization (~30s) but responds in ~5s
- Subsequent requests use fully-loaded RAG system (~2.5s response time)
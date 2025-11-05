# Overview

This is Maya, a UK-focused autism facts assistant built with FastAPI and vanilla JavaScript. The application provides a web-based chat interface for autistic people and carers to get information about UK autism support, services, benefits, and education. The system includes robust safety guardrails to prevent inappropriate medical, legal, or crisis advice, plus advanced accessibility features including reading comprehension level options for users with learning disabilities.

# Recent Changes

**November 5, 2025 (latest)**: Added reading comprehension level accessibility feature
- **ACCESSIBILITY BREAKTHROUGH**: Three reading levels (Clear/Standard/Complex) for users with learning disabilities
- **CLEAR LEVEL**: Simple everyday words targeting lower secondary school (Key Stage 3, ages 10-12)
- **STANDARD LEVEL**: Accessible language for general audiences (GCSE/A-Level, ages 14-18)
- **COMPLEX LEVEL**: Detailed technical language for adult/professional audiences
- **UI SELECTOR**: Dropdown in header with "Reading level:" label, persisted in localStorage
- **BACKEND VALIDATION**: Pydantic enum validation rejects invalid comprehension levels
- **LLM PROMPTS**: Comprehensive language guidelines adjust vocabulary, sentence length, and technical terms
- **TESTING**: All three levels produce visibly distinct language complexity
- **IMPACT**: Users with learning disabilities can now receive answers at appropriate comprehension levels

**October 31, 2025 (latest)**: Fixed deployment health check failures - PRODUCTION READY
- **CRITICAL FIX**: Resolved 5-minute timeout failures on autoscale deployments
- **DEPLOYMENT CONFIG**: Configured autoscale deployment with `healthCheckPath = "/health"` for proper health checks
- **ULTRA-FAST HEALTH**: `/health` endpoint responds in ~3ms with zero dependencies
- **NON-BLOCKING STARTUP**: Added 0.1s delay before background RAG initialization to ensure server fully starts first
- **LAZY IMPORTS**: Moved all heavy RAG imports inside background tasks (not at module level) to prevent blocking
- **FIRE-AND-FORGET**: Startup event spawns background task and completes immediately without awaiting
- **STARTUP ORDER**: Server starts in ~0.2s, RAG initializes in background thread pool (15-20s)
- **VERIFICATION**: "Application startup complete" log appears immediately, before RAG initialization begins
- **STATUS ENDPOINT**: `/status` provides `rag_ready` boolean for frontend monitoring of background initialization
- **CHAT BUBBLES**: Fixed responsive layout - bubbles now expand to 680px in wide mode (was stuck at 280px)
- **IMPACT**: Deployments pass health checks in <1 second, enabling successful production rollouts

**October 31, 2025 (earlier)**: Responsive layout with focus-first design
- **FEATURE**: Added optional width expansion toggle for carers and desktop users
- **DEFAULT**: Maintains 420px narrow focus mode for autism-friendly reduced overwhelm
- **EXPANDED**: Optional 960px width mode on tablets/desktop (min-width: 768px)
- **MOBILE BEHAVIOR**: Layout toggle hidden on mobile (< 768px) to prevent confusion - only appears when functional
- **PERSISTENCE**: Layout preference saved in localStorage per device
- **ACCESSIBILITY**: Keyboard accessible (Tab, Enter, Space), 44px touch targets, iOS safe-area support
- **TEXT CHANGE**: "NORMAL MODE" renamed to "NEUROTYPICAL MODE" for clarity
- **IMPACT**: Flexible layout accommodates both autistic users (narrow focus) and carers (expanded view)

**October 31, 2025 (earlier)**: Startup optimization with background initialization
- **PERFORMANCE BREAKTHROUGH**: Reduced warm response time from 10-15s to ~1.6s
- **DEPLOYMENT FIX**: Moved RAG initialization to background task to pass health checks immediately
- **IMPLEMENTATION**: Background async task initializes RAG system without blocking server startup
- **HEALTH CHECKS**: Server responds to health checks in ~0.2s, enabling successful deployments
- **COMPONENTS WARMED**: SentenceTransformer embedder, ChromaDB vector store, Groq client
- **NEW ENDPOINTS**: `/health` includes `rag_ready` status; `/warmup` exercises critical components
- **KEEP-ALIVE AUTOMATION**: GitHub Actions workflow pings `/warmup` every 5 minutes to prevent cold starts
- **LAZY LOADING**: Chat endpoint falls back to on-demand initialization if background task incomplete
- **TESTING**: Health check 0.2s, chat responses ~1.65s, deployment health checks pass
- **IMPACT**: Production-ready deployment with near-instant responses for users

**October 31, 2025 (earlier)**: Comprehensive structured knowledge dataset deployed
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
  - `/` - Serves main chat interface (FileResponse with no-cache headers)
  - `/health` - Ultra-fast health check endpoint (~3ms, zero dependencies) for deployment probes
  - `/status` - Extended status endpoint with RAG readiness info for frontend monitoring
  - `/warmup` - Component health check and keep-alive endpoint
  - `/chat` - Main conversation endpoint with safety guardrails
- **Startup Optimization**: Background async task uses `asyncio.to_thread()` to initialize RAG in thread pool
- **Non-Blocking Design**: Server starts in ~0.2s, responds to health checks immediately while RAG loads in background
- **Deployment Ready**: Autoscale deployment configured to use `/health` endpoint, passes health checks instantly
- **Lazy Loading**: Chat endpoint falls back to on-demand initialization if background task incomplete
- **Safety System**: Regex-based guardrail patterns detecting clinical, legal, and crisis content
- **Configuration**: Environment variable-based with UK timezone and locale settings
- **Keep-Alive**: GitHub Actions workflow prevents cold starts via periodic warmup pings

## Frontend Architecture
- **Technology Stack**: Vanilla HTML, CSS, and JavaScript for simplicity
- **UI Design**: Light theme with calm, autism-friendly color palette
- **Chat Interface**: MayaApp class managing real-time conversations
- **Responsive Design**: Mobile-first with optional width expansion
- **Layout Modes**:
  - **Focus Mode** (default): 420px narrow width for reduced overwhelm and easy reading
  - **Expanded Mode** (optional): 960px width for carers/desktop users who need more space
  - User preference saved in localStorage per device
- **Accessibility Features**:
  - **Reading comprehension levels**: Clear/Standard/Complex selector for users with learning disabilities
  - Low-stimulation mode toggle (removes colors, reduces visual complexity)
  - Neurotypical mode for users who prefer standard color schemes
  - Layout toggle button with keyboard support (Enter/Space)
  - 44px minimum touch targets for buttons
  - iOS safe-area support for notch devices
  - Respects prefers-reduced-motion for smooth transitions
  - All preferences persisted in localStorage

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

## Performance & Startup
- **Background initialization**: RAG system loads in background task without blocking server startup
- **Health check response**: ~0.2s (immediate), enabling deployment health checks to pass
- **Initialization time**: Background task completes in ~15-20s (ChromaDB + embeddings + Groq)
- **Chat response times**: ~1.6s (warm), lazy-loads if background initialization incomplete
- **Keep-alive**: GitHub Actions workflow pings `/warmup` every 5 minutes
- **Cold start prevention**: Configure `MAYA_WARMUP_URL` secret with production URL for automated pings
- **Deployment ready**: Server starts immediately, RAG initializes asynchronously for production compatibility
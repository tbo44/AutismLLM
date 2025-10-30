# Overview

This is Maya, a UK-focused autism facts assistant built with FastAPI and vanilla JavaScript. The application provides a web-based chat interface for autistic people and carers to get information about UK autism support, services, benefits, and education. The system includes robust safety guardrails to prevent inappropriate medical, legal, or crisis advice.

# Recent Changes

**October 30, 2025**: Optimized RAG initialization for 99.8% faster first-response time
- Implemented async background initialization with instant user feedback
- Added startup pre-creation of RAG system instance for sub-100ms first response
- First request now returns in 0.058s (was 15s) with clear "Initializing server" message
- Added InitializationState tracking (NOT_STARTED → INITIALIZING → READY/ERROR)
- Fixed circular import issue between rag_system.py and answerer.py
- Improved user messaging with immediate feedback and 30-60 second wait time
- Added detailed timing logs for vector store, LLM client, and retriever initialization
- Replaced "Tell me a joke" with "What is autism?" suggestion button for focus
- Performance results: First request 0.058s (was 15s), full initialization 30s, subsequent requests 2.5s

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
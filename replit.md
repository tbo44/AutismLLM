# Overview

Maya is a UK-focused autism facts assistant built with FastAPI and vanilla JavaScript. It provides a web-based chat interface for autistic people and carers to access information about UK autism support, services, benefits, and education. The application incorporates robust safety guardrails against inappropriate advice and advanced accessibility features, including reading comprehension level options for users with learning disabilities. The project aims to offer a reliable and accessible resource, leveraging a dual knowledge base system for comprehensive and accurate information.

# User Preferences

Preferred communication style: Simple, everyday language
Target audience: UK autistic people and carers
Jurisdiction: United Kingdom (emphasis on England and Hounslow)

# System Architecture

## Knowledge Base System
Maya utilizes dual knowledge sources:
- **Web Crawling System**: Extracts general autism information from trusted UK websites (NHS, NAS, Gov.UK).
- **Structured Knowledge Import**: Curated JSONL/CSV files containing detailed bureaucratic guides, prioritized in search with a relevance boost.

## Backend Architecture
- **Framework**: FastAPI with Python 3.11 for a robust API.
- **Core Endpoints**: `/` (main chat interface), `/health` (ultra-fast health check), `/status` (RAG readiness), `/warmup` (component health check), and `/chat` (main conversation with safety guardrails).
- **Startup Optimization**: Background async task using `asyncio.to_thread()` initializes RAG non-blockingly, allowing the server to start rapidly and respond to health checks immediately.
- **Deployment Ready**: Configured for autoscale deployment using the `/health` endpoint.
- **Safety System**: Regex-based guardrail patterns detect and refuse clinical, legal, or crisis content.
- **Configuration**: Environment variable-based with UK timezone and locale settings.

## Frontend Architecture
- **Technology Stack**: Vanilla HTML, CSS, and JavaScript.
- **UI Design**: Light theme with a calm, autism-friendly color palette.
- **Chat Interface**: Managed by the MayaApp class for real-time conversations.
- **Responsive Design**: Mobile-first with optional width expansion.
  - **Layout Modes**: Default 420px "Focus Mode" for reduced overwhelm, and an optional 960px "Expanded Mode" for carers/desktop users. User preference is saved in localStorage.
- **Accessibility Features**:
  - Reading comprehension levels (Clear/Standard/Complex) selector.
  - Low-stimulation mode toggle.
  - Keyboard accessible controls and 44px minimum touch targets.
  - Respects `prefers-reduced-motion` and includes iOS safe-area support. All preferences are persisted in localStorage.

## Safety & Compliance System
- **Guardrails**: Automatic detection and refusal of inappropriate content in clinical, legal, and crisis categories.
- **Refusal Templates**: Professional redirects to appropriate UK services.
- **Disclaimer**: Persistent banner emphasizing the information-only nature of the advice.

## Testing Architecture
- **Test Coverage**: Comprehensive tests for endpoints, guardrails, and static assets.
- **Policy Tests**: Parametrized tests for safety guardrail functionality.
- **Smoke Tests**: Basic functionality verification.
- **Static Asset Tests**: Verification of CSS, JavaScript, and HTML loading.

# External Dependencies

## Core Dependencies
- **FastAPI**: Web framework.
- **Uvicorn**: ASGI server.
- **Pydantic**: Data validation and API modeling.
- **pytz**: Timezone handling.

## Content Processing & RAG Implementation
- **ChromaDB**: Vector database for semantic search.
- **SentenceTransformers**: Text embeddings.
- **Groq**: Open-source LLM API client for response generation.
- **Trafilatura**: Web content extraction.
- **BeautifulSoup4**: HTML parsing.
- **lxml**: XML/HTML processing.
- **python-multipart**: Form data handling.
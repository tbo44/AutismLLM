# Overview

This is a personal LLM (Large Language Model) chat application built with FastAPI and vanilla JavaScript. The application provides a web-based interface for interacting with OpenAI's language models, featuring a modern dark-themed UI and configurable model parameters. Users can engage in conversations with AI assistants through a clean, responsive chat interface.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Backend Architecture
- **Framework**: FastAPI with Python, chosen for its modern async capabilities and automatic API documentation
- **API Design**: RESTful endpoints with Pydantic models for request/response validation
- **Configuration**: Environment variable-based configuration using python-dotenv for flexibility across environments
- **CORS Policy**: Permissive CORS configuration allowing all origins for development/personal use

## Frontend Architecture
- **Technology Stack**: Vanilla HTML, CSS, and JavaScript without frameworks to minimize complexity
- **UI Design**: Dark theme with modern CSS using system fonts and flexbox layouts
- **Client Architecture**: Class-based JavaScript architecture with ChatApp class managing state and interactions
- **Responsive Design**: Mobile-first approach with flexible layouts

## Chat System Design
- **Message Management**: In-memory message history maintained on the frontend
- **Real-time Interaction**: Synchronous API calls with visual feedback (thinking indicators)
- **User Experience**: Auto-focus input, keyboard shortcuts (Enter to send), and disabled states during processing

## Configuration Management
- **Model Selection**: Runtime configurable model selection through environment variables with UI override
- **Temperature Control**: Adjustable creativity/randomness parameter (0-2 range) with UI controls
- **System Prompts**: Configurable system behavior through environment variables

# External Dependencies

## AI Service Integration
- **OpenAI API**: Primary language model provider with configurable base URL for potential alternative providers
- **Authentication**: API key-based authentication stored in environment variables
- **Models**: Defaults to gpt-4o-mini with support for other OpenAI-compatible models

## Development Dependencies
- **FastAPI**: Web framework for building the API backend
- **Uvicorn**: ASGI server for running the FastAPI application
- **Pydantic**: Data validation and settings management
- **python-dotenv**: Environment variable management from .env files

## Static File Serving
- **Static Assets**: CSS, JavaScript, and HTML files served directly by FastAPI
- **No CDN Dependencies**: All frontend assets are self-hosted for offline capability
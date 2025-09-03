# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PaperAgent is an AI-powered academic paper writing assistant that supports automated paper generation with integrated code execution, data analysis, and visualization capabilities. The system uses a multi-agent AI architecture with specialized agents for different tasks.

## Development Commands

### Frontend (Vue 3 + TypeScript)
```bash
cd frontend

# Install dependencies
npm install

# Development server
npm run dev

# Type checking
npm run type-check

# Build for production
npm run build

# Format code
npm run format

# Preview build
npm run preview
```

### Backend (FastAPI + Python)
```bash
cd backend

# Install uv package manager
pip install uv

# Create virtual environment and install dependencies
uv venv
uv pip install -r pyproject.toml

# Activate virtual environment
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows CMD
.venv\Scripts\activate.bat
# Linux/macOS
source .venv/bin/activate

# Start development server
uv run main.py
```

## Architecture Overview

### Frontend Architecture
- **Framework**: Vue 3 with TypeScript using Composition API
- **UI Library**: TDesign Vue Next for components
- **State Management**: Pinia with persistence
- **Routing**: Vue Router 4
- **Build Tool**: Vite
- **Styling**: CSS with TDesign theme system

### Backend Architecture
- **API Framework**: FastAPI with async support
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Authentication**: JWT-based auth
- **AI Integration**: LiteLLM for multi-model support
- **WebSocket**: Real-time communication for streaming responses
- **Code Execution**: Secure Python code execution in isolated environments

### AI System Architecture
The AI system uses a multi-agent approach:

1. **MainAgent** (`backend/ai_system/core/main_agent.py`): Primary orchestrator that analyzes user requests and delegates tasks
2. **CodeAgent** (`backend/ai_system/core/agents.py`): Specialized in code execution and data analysis
3. **TemplateAgent**: Handles template processing and paper structure generation
4. **Tools Framework**: Modular tool system for file operations, code execution, and template processing

### Key Components

#### Frontend Structure
- `src/views/`: Page components (Home, Demo, ApiKeyConfig, etc.)
- `src/components/`: Reusable UI components (FileManager, Chat components, etc.)
- `src/api/`: API client modules for backend communication
- `src/stores/`: Pinia state management stores
- `src/composables/`: Vue composition utilities

#### Backend Structure
- `routers/`: FastAPI route definitions for different API endpoints
- `models/`: SQLAlchemy database models
- `database/`: Database connection and configuration
- `ai_system/`: Core AI system implementation
  - `core/`: Agent classes and LLM handling
  - `tools/`: Specialized tools for different tasks
  - `config/`: Configuration management
- `auth/`: Authentication and authorization logic

#### Workspace Management
- Each user work session creates a unique workspace in `pa_data/workspaces/`
- Workspaces contain generated files, code, and paper drafts
- File operations are sandboxed to workspace directories

## Development Workflow

### Environment Setup
1. Copy `.env.example` to `.env` in both frontend and backend directories
2. Configure database connection in backend `.env`
3. Set up AI model API keys in the admin interface

### Testing
- Backend uses pytest (already configured in pyproject.toml)
- Frontend type checking with `npm run type-check`
- No dedicated test commands yet - run tests directly with pytest

### Database Operations
- Models are defined in `backend/models/models.py`
- Database migrations are handled automatically on startup
- Schema changes require updating the model definitions

## Important Configuration

### Frontend Environment Variables
- `VITE_API_BASE_URL`: Backend API base URL
- `VITE_APP_TITLE`: Application title
- `VITE_APP_VERSION`: Application version

### Backend Environment Variables
- `DATABASE_URL`: PostgreSQL connection string
- `SECRET_KEY`: JWT secret key
- `ACCESS_TOKEN_EXPIRE_MINUTES`: Token expiration time

### AI Model Configuration
- AI models are configured through the admin interface
- Supports multiple providers (OpenAI, Anthropic, Google, etc.)
- Model selection is handled by the MainAgent based on task requirements

## File Structure Conventions

### Frontend
- Use Vue 3 Composition API with `<script setup>`
- TypeScript for all new code
- TDesign components for UI consistency
- Pinia stores for state management
- API calls centralized in `src/api/` modules

### Backend
- Async/await patterns throughout
- Pydantic models for request/response validation
- SQLAlchemy ORM for database operations
- Dependency injection via FastAPI
- Modular tool system for extensibility

### AI System
- Agent-based architecture with clear separation of concerns
- Streaming responses for real-time feedback
- Context management for conversation continuity
- Tool-based approach for task execution

## Security Considerations

- Code execution happens in isolated environments
- File operations are restricted to workspace directories
- JWT-based authentication with configurable expiration
- CORS properly configured for cross-origin requests
- Input validation on all API endpoints
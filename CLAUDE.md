# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.项目使用UV！！！！

### Claude Code 八荣八耻

- 以瞎猜接口为耻，以认真查询为荣。
- 以模糊执行为耻，以寻求确认为荣。
- 以臆想业务为耻，以复用现有为荣。
- 以创造接口为耻，以主动测试为荣。
- 以跳过验证为耻，以人类确认为荣。
- 以破坏架构为耻，以遵循规范为荣。
- 以假装理解为耻，以诚实无知为荣。
- 以盲目修改为耻，以谨慎重构为荣。

## Project Overview

PaperAgent is an AI-powered academic paper writing assistant that helps researchers, students, and academic writers generate high-quality papers efficiently. The system features a multi-AI agent architecture with specialized agents for different tasks.

## Architecture

### Frontend (Vue 3 + TypeScript)
- **Location**: `frontend/`
- **Tech Stack**: Vue 3, TypeScript, TDesign Vue Next, Pinia, Vite
- **Key Features**: Real-time paper preview, file management, AI chat interface, template system

### Backend (FastAPI + Python)
- **Location**: `backend/`
- **Tech Stack**: FastAPI, SQLAlchemy, PostgreSQL, LiteLLM, WebSocket
- **AI System**: Multi-agent architecture with MainAgent (controller) and CodeAgent (code execution)

### AI System Architecture
```
ai_system/
├── MainAgent (main control AI)
│   ├── Task analysis and planning
│   ├── Model scheduling coordination
│   └── Context management
├── CodeAgent (code execution AI)
│   ├── Python code execution
│   ├── Data analysis and processing
│   └── Visualization chart generation
└── Tools Framework
    ├── File operation tools
    ├── Template processing tools
    └── Code execution tools
```

## Development Commands

### Backend Development
```bash
cd backend

# Setup environment
pip install uv
uv venv
uv pip install -r pyproject.toml

# Activate virtual environment
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Windows CMD: .venv\Scripts\activate.bat
# Linux/macOS: source .venv/bin/activate

# Start development server
uv run main.py

# Run tests
uv run pytest
```

### Frontend Development
```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev

# Type checking
npm run type-check

# Build for production
npm run build

# Preview production build
npm run preview

# Format code
npm run format
```

## Key Configuration

### Backend Environment (.env)
```env
DATABASE_URL=postgresql://user:password@localhost:5432/paperagent
SECRET_KEY=your-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

### Frontend Environment (.env)
```javascript
VITE_API_BASE_URL=
VITE_APP_TITLE=
VITE_APP_VERSION=
```

## Project Structure

### Backend Directories
- `ai_system/` - Core AI system with multi-agent architecture
- `routers/` - API route definitions
- `models/` - Database models
- `database/` - Database configuration and connection
- `services/` - Business logic services
- `schemas/` - Pydantic schemas for API
- `auth/` - Authentication and authorization

### Frontend Directories
- `src/components/` - Vue components
- `src/views/` - Page views
- `src/stores/` - Pinia state management
- `src/router/` - Vue Router configuration
- `src/api/` - API client functions
- `src/utils/` - Utility functions

## Database

- **Type**: PostgreSQL
- **ORM**: SQLAlchemy 2.0+ with async support
- **Migrations**: Automatic table creation on startup (for development)

## AI Integration

- **Provider**: LiteLLM (supports OpenAI, Anthropic, Google, etc.)
- **Model Configuration**: Managed through admin interface
- **Features**: Multi-model coordination, context sharing, specialized task agents

## Development Notes

### AI System Development
- The AI system uses a multi-agent approach where different agents handle different aspects of paper generation
- MainAgent coordinates the overall task and delegates to specialized agents
- CodeAgent handles Python code execution, data analysis, and visualization
- Context is shared between agents to maintain consistency

### Frontend Development
- Uses TDesign Vue Next for UI components
- Pinia for state management with persistence
- Real-time updates via WebSocket connections
- Supports LaTeX mathematical expressions via KaTeX

### Backend Development
- FastAPI with async/await throughout
- WebSocket support for real-time communication
- Modular architecture with clear separation of concerns
- Comprehensive error handling and logging

## Testing

- **Backend**: pytest with async support
- **Frontend**: Vue Test Utils (testing setup available)

## Deployment

The project is designed for deployment with:
- Frontend: Can be deployed to Vercel, Netlify, or similar platforms
- Backend: Suitable for containerization with Docker
- Database: PostgreSQL (external service or self-hosted)

## Important Notes

- The project does not use HTTPS for the frontend due to backend hosting constraints in China
- Mobile responsiveness is limited due to the complex nature of the paper generation interface
- The system is optimized for desktop use where users can benefit from the full three-panel interface (AI chat, file manager, live preview)
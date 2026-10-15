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
- **Tech Stack**: FastAPI, SQLAlchemy, PostgreSQL, LangChain + LangGraph, WebSocket
- **AI System**: LangGraph state-graph pipeline (`ai_system/graph/`) with a sandboxed Python kernel for computation

### AI System Architecture
```
ai_system/
├── graph/      LangGraph pipeline: analyze → design → solve → reconcile → write → validate → render → export
├── sandbox/    Python kernel the solve stage runs code in
├── export/     paper.md → paper.docx / paper.tex + paper.pdf (pandoc, xelatex)
├── judge/      paper scoring for evals
├── core_handlers/  LLM provider setup
├── core_managers/  stream manager (WebSocket JSON blocks)
└── core_tools/     template docx image/style inspection
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

# End-to-end eval on MM-Bench (see docs/evaluation.md)
uv run python -m evals all --bench /path/to/LLM-MM-Agent/MMBench --out eval_runs/<name> [--pipeline v2]
```

### State-graph pipeline (v2)
`backend/ai_system/graph/` is the 数模 paper pipeline (see docs/paper_graph.md). The chat API uses it for markdown / word / latex output, with or without a user template (rendered `paper.md` is exported by `ai_system/export/` to `paper.docx` or `paper.tex`/`paper.pdf`). The old MainAgent/CodeAgent/WriterAgent/ReviewAgent code was deleted; check out `12a9041` to rerun the legacy eval baseline. Numbers in the paper must come from the result registry (`{{R:Rn}}` / `{{F:Fn}}` references), which only accepts values that appear in a successful kernel run's output.

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
- `ai_system/` - Paper pipeline, sandbox, export and judge
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

- **Provider**: LangChain chat models (OpenAI-compatible, Anthropic, Google)
- **Model Configuration**: Managed through admin interface
- **Features**: separate models per role (brain / code / writing configs)

## Development Notes

### AI System Development
- The chat API runs `PaperGraphAgent` (`ai_system/graph/agent.py`); each node is a plain async function over `PaperState`
- Computation happens in the sandbox kernel; numbers reach the paper only through the result registry
- Progress reaches the frontend as `paper_*` JSON blocks (see docs/paper_graph.md)

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
- Desktop keeps the side-by-side chat + preview workspace; phones (≤768px) use a single-panel layout with bottom tabs for chat, files, and preview
- Shared breakpoint logic lives in `frontend/src/composables/useBreakpoint.ts` and tracks viewport changes via `matchMedia`
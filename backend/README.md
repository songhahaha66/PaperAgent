# PaperAgent Backend

This is the backend API for PaperAgent, built with FastAPI.

## Features
- RESTful API
- Real-time communication with WebSocket
- PostgreSQL database integration
- User authentication and authorization
- Task scheduling and management

## Requirements
- Python 3.10+
- PostgreSQL
- uv (Python package manager)

## Installation
1. Install dependencies with uv:
   ```
   uv sync
   ```

2. Copy `.env.example` to `.env` and configure your environment variables:
   ```
   cp .env.example .env
   ```

3. Run the development server:
   ```
   uv run main.py
   ```

The API will be available at http://localhost:8000
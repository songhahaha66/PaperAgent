"""
MCP Client Manager for Office-Word-MCP-Server integration
Manages MCP client connection and provides Word tools to LangChain agents
"""

import logging
from typing import List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manages MCP client connection and Word tools"""

    def __init__(self):
        """Initialize MCP client with word-mcp configuration"""
        self.client = None
        self.session = None
        self.word_tools = []
        self._initialized = False
        logger.info("MCPClientManager created")

    async def initialize(self) -> bool:
        """Initialize MCP session and load tools
        
        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            # Get the path to word_mcp_server.py
            # __file__ is in backend/ai_system/core_managers/mcp_client_manager.py
            # We need to go up to backend/ directory
            backend_dir = Path(__file__).parent.parent.parent
            word_mcp_path = backend_dir / "Office-Word-MCP-Server" / "word_mcp_server.py"
            
            if not word_mcp_path.exists():
                logger.error(f"Word MCP server not found at: {word_mcp_path}")
                return False

            logger.info(f"Initializing MCP client with word-mcp at: {word_mcp_path}")

            # Configure MCP client
            self.client = MultiServerMCPClient({
                "word": {
                    "transport": "stdio",
                    "command": "python",
                    "args": [str(word_mcp_path)],
                }
            })

            # Note: We don't start the session here. The session will be managed
            # by the context manager when tools are actually used.
            # This is a simpler approach that avoids lifecycle management issues.
            
            self._initialized = True
            logger.info(f"MCP client configured successfully")
            
            return True

        except ImportError as e:
            logger.error(f"Failed to import MCP dependencies: {e}")
            logger.error("Please ensure langchain-mcp-adapters is installed")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize MCP client: {e}", exc_info=True)
            return False

    async def cleanup(self) -> None:
        """Cleanup MCP client"""
        try:
            logger.info("Cleaning up MCP client...")
            # The client manages its own lifecycle
            self.client = None
            self.session = None
            self.word_tools = []
            self._initialized = False
            logger.info("MCP client cleaned up successfully")
        except Exception as e:
            logger.error(f"Error during MCP client cleanup: {e}", exc_info=True)

    def is_available(self) -> bool:
        """Check if MCP client is configured
        
        Returns:
            bool: True if MCP client is initialized
        """
        return self._initialized and self.client is not None

    def get_client(self):
        """Get the MCP client for creating sessions
        
        Returns:
            MultiServerMCPClient: The configured MCP client
        """
        if not self.is_available():
            logger.warning("MCP client requested but not available")
            return None
        
        return self.client

    def __repr__(self) -> str:
        """String representation"""
        status = "configured" if self._initialized else "not configured"
        return f"MCPClientManager(status={status})"

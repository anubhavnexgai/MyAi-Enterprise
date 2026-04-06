"""MCP (Model Context Protocol) client for MyAi."""
from __future__ import annotations
import logging
import os
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class MCPClient:
    """Connects to MCP servers and exposes their tools to MyAi."""

    def __init__(self):
        self._servers: dict[str, dict] = {}
        self._tools: dict[str, dict] = {}
        self._config_path = Path(__file__).parent.parent.parent / "config" / "mcp_servers.json"

    @property
    def is_configured(self) -> bool:
        return len(self._servers) > 0

    def load_config(self):
        """Load MCP server configurations from config file."""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r") as f:
                    config = json.load(f)
                self._servers = config.get("servers", {})
                logger.info(f"Loaded {len(self._servers)} MCP server configs")
            except Exception as e:
                logger.warning(f"Failed to load MCP config: {e}")

    async def discover_tools(self) -> list[dict]:
        """Discover available tools from all configured MCP servers."""
        all_tools = []
        for name, config in self._servers.items():
            try:
                # MCP uses stdio or SSE transport
                transport = config.get("transport", "stdio")
                command = config.get("command", "")
                args = config.get("args", [])

                if transport == "stdio" and command:
                    # Start the MCP server process and list tools
                    import subprocess
                    # This is a simplified discovery — full MCP uses JSON-RPC
                    logger.info(f"MCP server '{name}' configured: {command}")
                    all_tools.append({
                        "server": name,
                        "command": command,
                        "status": "configured",
                    })
            except Exception as e:
                logger.warning(f"MCP discovery failed for {name}: {e}")

        self._tools = {t["server"]: t for t in all_tools}
        return all_tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """Call a tool on an MCP server."""
        if server_name not in self._servers:
            return f"MCP server '{server_name}' not configured."

        config = self._servers[server_name]
        command = config.get("command", "")
        args = config.get("args", [])

        try:
            import subprocess
            # Build JSON-RPC request
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                }
            }

            proc = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=0x08000000,
            )
            stdout, stderr = proc.communicate(
                input=json.dumps(request).encode(),
                timeout=30,
            )

            if stdout:
                response = json.loads(stdout.decode())
                result = response.get("result", {})
                content = result.get("content", [])
                if content:
                    return content[0].get("text", str(result))
                return str(result)

            return f"MCP call returned no output. stderr: {stderr.decode()[:200]}"

        except Exception as e:
            return f"MCP call failed: {str(e)[:200]}"

    def list_servers(self) -> list[dict]:
        """List all configured MCP servers."""
        return [
            {"name": name, "command": config.get("command", ""), "transport": config.get("transport", "stdio")}
            for name, config in self._servers.items()
        ]

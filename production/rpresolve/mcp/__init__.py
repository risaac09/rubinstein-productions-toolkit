"""
rpresolve.mcp: a stdlib MCP server (JSON-RPC 2.0 over stdio) that exposes
the toolkit to Claude Code. Entry point: production/resolve_mcp.py.

Modules: stdio (fd isolation and framing), protocol (JSON-RPC and MCP),
schema (argument validation), registry (tools and their context), session
(the Resolve connection), and the tools_* modules.
"""

SERVER_NAME = "rpresolve"
SERVER_TITLE = "DaVinci Resolve (Rubinstein toolkit)"
__version__ = "0.1.0"

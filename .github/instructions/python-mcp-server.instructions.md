---
applyTo: "kite/**,mcp_server.py,search/**"
---
# MCP Server Guidelines for Artha

- All MCP tool implementations must handle malformed payloads gracefully and never raise unhandled exceptions.
- Tool schemas must be kept in sync with tests/test_tools.py assertions.
- The mcp_server.py exposes Artha as an MCP server — ensure no order-placement tools are ever added. Read-only analysis only.
- NSE India stdio MCP prints banner text before JSON-RPC frames; any wrapper must strip this before parsing.
- Yahoo Finance and NSE India are the approved enrichment sources. Do not add Alpha Vantage.

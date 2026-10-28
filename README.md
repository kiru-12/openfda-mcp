# openFDA MCP server

A small, real-world [MCP](https://modelcontextprotocol.io) server for learning how MCP works.
It gives Claude three tools that query the public [openFDA](https://open.fda.gov) API:

| Tool | What it does |
|---|---|
| `search_drug_labels(query, limit)` | Find labels by brand or generic name; returns names, manufacturer, set_id |
| `get_label_section(drug, section)` | Return one section of the most recent label (boxed_warning, indications, dosage, ...) |
| `get_adverse_events(drug, limit)` | Top reported reactions from FAERS (spontaneous reports, not incidence) |

It also exposes one resource, `openfda://label-sections`, listing the accepted section names.

## How it fits together

```
Claude (host)  <-- MCP over stdio -->  server.py  <-- HTTPS -->  api.fda.gov
```

Claude never calls openFDA. It only sees the tool names, descriptions, and input schemas
that `server.py` advertises. When Claude calls a tool, the SDK validates the arguments,
runs the Python function, and returns the string as a text result.

Everything Claude knows about a tool comes from the function signature and docstring.
Type hints become the JSON schema; the docstring becomes the description.

## Setup

Requires `uv` (installed) and Python 3.10+ (uv downloads 3.12 into `.venv`; your system Python is untouched).

```bash
uv sync
```

## Try it

**Standalone, from a client:** the MCP Inspector gives you a web UI to list tools and call them.

```bash
npx @modelcontextprotocol/inspector uv run server.py
```

**From Claude Code:** already registered at user scope. In any Claude Code session run `/mcp`
to see it, then ask something like:

> Using the openfda tools, what is the boxed warning for apixaban, and what are the top 5
> reported adverse events?

Registration command used (for reference):

```bash
claude mcp add --scope user openfda -e HTTP_PROXY=... -e HTTPS_PROXY=... -e NO_PROXY=... -- uv --directory C:\Users\sk6\Downloads\openfda-mcp run server.py
```

**From Claude Desktop:** add to `%APPDATA%\Claude\claude_desktop_config.json` and restart the app.

```json
{
  "mcpServers": {
    "openfda": {
      "command": "uv",
      "args": ["--directory", "C:\Users\sk6\Downloads\openfda-mcp", "run", "server.py"],
      "env": {
        "HTTP_PROXY": "http://your-proxy-host:port",
        "HTTPS_PROXY": "http://your-proxy-host:port",
        "NO_PROXY": "localhost,127.0.0.1,::1,.local"
      }
    }
  }
}
```

## Gotchas learned while building this

- **Hosts launch servers with a stripped environment.** The MCP SDK forwards only ~12 safe
  variables (PATH, TEMP, USERPROFILE, ...). Proxy variables are dropped, so they must be set
  explicitly in the host config (`-e` for Claude Code, `"env"` for Claude Desktop).
- **Raise `ToolError`, not plain exceptions.** The SDK forwards `ToolError` messages to Claude
  verbatim; any other exception is reported as a generic "Error executing tool".
- **Keep results small.** A full label is tens of thousands of characters. Return one section
  at a time and truncate, or you will flood Claude's context.
- **mcp 2.x renamed `FastMCP` to `MCPServer`.** Older tutorials show
  `from mcp.server.fastmcp import FastMCP`; here it is `from mcp.server import MCPServer`.
- **openFDA rate limit** is 240 requests/min and 1,000/day without a key. A free key
  (`api_key` query param) raises it; not needed for learning.

## Ideas to extend

- Add a `compare_labels(drug_a, drug_b, section)` tool that calls openFDA twice.
- Add a recall lookup against `/drug/enforcement.json`.
- Switch `mcp.run(transport="stdio")` to `transport="streamable-http"` and connect to it remotely.
- Add an `@mcp.prompt()` that templates a "safety summary for drug X" request.

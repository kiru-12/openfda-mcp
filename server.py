"""
openFDA MCP server - a small, real-world sample for learning how MCP works.

Claude never calls openFDA directly. It only sees the tools declared below,
calls them with arguments, and reads the text they return. This file is the
adapter between the MCP protocol and the openFDA REST API.

Run standalone:        uv run server.py
Inspect interactively: npx @modelcontextprotocol/inspector uv run server.py
"""

from __future__ import annotations

import logging

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# httpx logs every request at INFO; keep stderr quiet so hosts don't show noise.
logging.getLogger("httpx").setLevel(logging.WARNING)

OPENFDA = "https://api.fda.gov"
TIMEOUT = 20.0
MAX_CHARS = 6000  # keep a single tool result from flooding Claude's context

# Human-friendly names -> field names in the openFDA drug label dataset.
SECTIONS = {
    "boxed_warning": "boxed_warning",
    "indications": "indications_and_usage",
    "dosage": "dosage_and_administration",
    "contraindications": "contraindications",
    "warnings": "warnings_and_cautions",
    "adverse_reactions": "adverse_reactions",
    "drug_interactions": "drug_interactions",
    "specific_populations": "use_in_specific_populations",
    "mechanism": "mechanism_of_action",
    "pharmacology": "clinical_pharmacology",
    "how_supplied": "how_supplied",
}

mcp = MCPServer(
    "openfda",
    instructions=(
        "Tools for querying FDA drug labeling and adverse event reports via openFDA. "
        "Start with search_drug_labels to confirm the drug exists and see its names, "
        "then use get_label_section for specific label text."
    ),
)


async def _get(path: str, params: dict) -> dict:
    """Call openFDA and translate HTTP errors into readable messages."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(f"{OPENFDA}{path}", params=params)
    if resp.status_code == 404:
        raise ToolError("openFDA returned no matches for that query.")
    if resp.status_code == 429:
        raise ToolError("openFDA rate limit hit (240 requests/min without an API key). Try again shortly.")
    resp.raise_for_status()
    return resp.json()


def _truncate(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS] + f"\n\n[... truncated, {len(text) - MAX_CHARS} more characters]"


def _label_query(drug: str) -> str:
    # Match the drug by brand OR generic name. Quotes make it a phrase match.
    return f'openfda.brand_name:"{drug}" OR openfda.generic_name:"{drug}"'


# --------------------------------------------------------------------------- #
# Tools: functions Claude can call. Type hints become the input schema and the
# docstring becomes the description Claude reads when deciding what to call.
# --------------------------------------------------------------------------- #


@mcp.tool()
async def search_drug_labels(query: str, limit: int = 5) -> str:
    """Search FDA drug labels by brand or generic name.

    Use this first to confirm a drug exists in openFDA and to see its exact
    brand name, generic name, and manufacturer before requesting label text.

    Args:
        query: Brand or generic drug name, e.g. "Eliquis" or "apixaban".
        limit: Maximum number of labels to return (1-25).
    """
    limit = max(1, min(limit, 25))
    data = await _get(
        "/drug/label.json",
        {"search": _label_query(query), "sort": "effective_time:desc", "limit": limit},
    )
    lines = []
    for i, r in enumerate(data.get("results", []), 1):
        of = r.get("openfda", {})
        brand = ", ".join(of.get("brand_name", ["?"]))
        generic = ", ".join(of.get("generic_name", ["?"]))
        maker = ", ".join(of.get("manufacturer_name", ["?"]))
        route = ", ".join(of.get("route", ["?"]))
        lines.append(
            f"{i}. {brand} ({generic})\n   Manufacturer: {maker}\n   Route: {route}\n"
            f"   Label updated: {r.get('effective_time', '?')}  set_id: {r.get('set_id', '?')}"
        )
    total = data.get("meta", {}).get("results", {}).get("total", len(lines))
    return f"{total} label(s) match '{query}'. Showing {len(lines)}:\n\n" + "\n".join(lines)


@mcp.tool()
async def get_label_section(drug: str, section: str = "indications") -> str:
    """Return one section of the current FDA label for a drug.

    Args:
        drug: Brand or generic name, e.g. "apixaban".
        section: One of: boxed_warning, indications, dosage, contraindications,
            warnings, adverse_reactions, drug_interactions, specific_populations,
            mechanism, pharmacology, how_supplied.
    """
    key = section.lower().strip()
    if key not in SECTIONS:
        raise ToolError(f"Unknown section '{section}'. Choose from: {', '.join(SECTIONS)}")
    field = SECTIONS[key]

    data = await _get(
        "/drug/label.json",
        {"search": _label_query(drug), "sort": "effective_time:desc", "limit": 1},
    )
    label = data["results"][0]
    of = label.get("openfda", {})
    name = f"{', '.join(of.get('brand_name', ['?']))} ({', '.join(of.get('generic_name', ['?']))})"

    text = label.get(field)
    if not text:
        present = [k for k, v in SECTIONS.items() if label.get(v)]
        return f"{name}: the label has no '{key}' section. Sections present: {', '.join(present)}"
    body = "\n".join(text) if isinstance(text, list) else str(text)
    return f"{name} - {key.replace('_', ' ').title()}\n(label effective {label.get('effective_time', '?')})\n\n{_truncate(body)}"


@mcp.tool()
async def get_adverse_events(drug: str, limit: int = 10) -> str:
    """Return the most frequently reported adverse reactions for a drug from FAERS.

    FAERS is the FDA Adverse Event Reporting System. Counts are spontaneous
    reports and do not imply causation or incidence.

    Args:
        drug: Brand or generic name, e.g. "apixaban".
        limit: Number of reactions to return (1-50).
    """
    limit = max(1, min(limit, 50))
    data = await _get(
        "/drug/event.json",
        {
            "search": f'patient.drug.medicinalproduct:"{drug}" OR patient.drug.openfda.generic_name:"{drug}"',
            "count": "patient.reaction.reactionmeddrapt.exact",
            "limit": limit,
        },
    )
    rows = data.get("results", [])
    if not rows:
        return f"No adverse event reports found for '{drug}'."
    width = max(len(r["term"]) for r in rows)
    lines = [f"{r['term']:<{width}}  {r['count']:>8,}" for r in rows]
    return (
        f"Top {len(rows)} reported reactions for '{drug}' (FAERS, all time):\n\n"
        + "\n".join(lines)
        + "\n\nNote: spontaneous reports; not incidence rates."
    )


# --------------------------------------------------------------------------- #
# Resource: read-only data Claude can load by URI, as opposed to a tool it calls.
# --------------------------------------------------------------------------- #


@mcp.resource("openfda://label-sections")
def label_sections() -> str:
    """List of label section names accepted by get_label_section."""
    return "\n".join(f"{k} -> {v}" for k, v in SECTIONS.items())


if __name__ == "__main__":
    mcp.run(transport="stdio")

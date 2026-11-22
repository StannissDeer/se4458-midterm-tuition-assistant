"""MCP server (official MCP Python SDK v2) exposing search_tuition_policies(query).

Run:  python server.py                  -> Streamable HTTP on 0.0.0.0:8100/mcp
      MCP_TRANSPORT=stdio python server.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from indexer import load_or_build  # noqa: E402

INDEX = load_or_build()  # built once at start-up, never per query



class PolicyMatch(BaseModel):
    section: str = Field(description="Section ID to cite, e.g. '3.2'")
    heading: str = Field(description="Section heading, e.g. 'Number and size of installments'")
    parentSection: str = Field(description="Top-level section, e.g. '3. Installment policy'")
    score: float = Field(description="BM25 relevance score")
    text: str = Field(description="Policy text of the section")


class PolicySearchResult(BaseModel):
    """Shape follows Section 9 of the policy document: document, document ID and matches."""
    document: str = "University_Tuition_Policies.md"
    documentId: str = Field(default="", description="e.g. UNI-TUITION-2026-01")
    query: str
    matches: list[PolicyMatch]
    message: str | None = None


mcp = MCPServer(
    name="tuition-policies",
    instructions="Search the University Tuition Policies document. Results include section "
    "references (e.g. §4.1) that should be cited in answers.",
)


@mcp.tool(
    name="search_tuition_policies",
    structured_output=True,
    description="Full-text search over the indexed University Tuition Policies document "
    "(deadlines, installments, late fees, scholarships, refunds, banking instructions). "
    "Returns the most relevant passages with section ID, heading, document ID and text. This is policy text only - "
    "it does NOT contain any student's balance or records.",
)
def search_tuition_policies(query: str, top_k: int = 4) -> PolicySearchResult:
    """query: natural-language question or keywords. top_k: number of passages (1-5)."""
    query = (query or "").strip()
    if not query:
        return PolicySearchResult(documentId=INDEX.doc_id, query=query, matches=[], message="Empty query")
    top_k = max(1, min(int(top_k), 5))
    results = INDEX.search(query, top_k)
    return PolicySearchResult(
        documentId=INDEX.doc_id,
        query=query,
        matches=[PolicyMatch(**r) for r in results],
        message=None if results else "No matching policy section found",
    )


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run(
            "streamable-http",
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_PORT", "8100")),
            streamable_http_path="/mcp",
        )

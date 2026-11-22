"""MCP server + index tests (no network: the MCP client connects to the server in-process)."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import Client  # noqa: E402

import server  # noqa: E402
from indexer import BM25Index, build_index, split_markdown  # noqa: E402


def top_sections(query, k=4):
    return [r["section"] for r in server.INDEX.search(query, k)]


# ---------- chunking / indexing ----------
def test_document_is_split_into_section_chunks():
    chunks = split_markdown((ROOT / "docs" / "University_Tuition_Policies.md").read_text(encoding="utf-8"))
    ids = [c.id for c in chunks]
    assert ids[:2] == ["1", "2.1"] and "3.2" in ids and "7.3" in ids
    assert all(c.text for c in chunks)
    elig = next(c for c in chunks if c.id == "3.1")
    assert elig.heading == "Eligibility" and elig.parent == "3. Installment policy"
    assert "$300" in elig.text


def test_document_id_is_extracted():
    assert server.INDEX.doc_id == "UNI-TUITION-2026-01"


def test_index_is_persisted_and_reloaded(tmp_path):
    out = tmp_path / "index.json"
    built = build_index(out=out)
    loaded = BM25Index.load(out)
    assert len(loaded.chunks) == len(built.chunks) and loaded.doc_id == built.doc_id
    assert loaded.search("late fee")[0]["section"] == built.search("late fee")[0]["section"]


# ---------- three distinct retrieval queries ----------
def test_retrieval_fall_deadline():
    top = top_sections("When is the Fall payment deadline?")
    assert "2.1" in top or "8.1" in top


def test_retrieval_installments():
    top = top_sections("Can I pay my outstanding balance in installments?")
    assert "3.1" in top or "8.2" in top


def test_retrieval_late_fee():
    assert top_sections("late fee for overdue payment")[0] == "4.1"


def test_retrieval_refund():
    assert top_sections("refund if I withdraw")[0] == "6.1"


def test_no_match_returns_empty():
    assert server.INDEX.search("quantum chromodynamics") == []


# ---------- real MCP protocol ----------
def test_mcp_tool_is_listed_and_callable():
    async def run():
        async with Client(server.mcp) as c:
            tools = await c.list_tools()
            assert [t.name for t in tools.tools] == ["search_tuition_policies"]
            assert tools.tools[0].input_schema["required"] == ["query"]
            assert tools.tools[0].output_schema is not None
            res = await c.call_tool("search_tuition_policies", {"query": "late fee"})
            data = res.structured_content
            assert data["document"] == "University_Tuition_Policies.md"
            assert data["documentId"] == "UNI-TUITION-2026-01"
            m = data["matches"][0]
            assert m["section"] == "4.1" and m["heading"] == "Late fee rule" and "2%" in m["text"]

    asyncio.run(run())


def test_mcp_empty_query_edge_case():
    assert server.search_tuition_policies("   ").matches == []

"""Agent tests with a scripted fake LLM (no Gemini key needed), the real MCP server in-process
and the real REST API over an in-memory ASGI transport (SQLite)."""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from google.genai import types
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "agent", ROOT / "mcp_server", ROOT / "api"):
    sys.path.insert(0, str(p))

import server as mcp_server  # noqa: E402
from agent import TuitionAgent, format_trace  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Student, Tuition, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from backends import PolicyBackend, TuitionBackend  # noqa: E402


# ---------------------------------------------------------------- fake LLM
def call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def text(t):
    return types.Part(text=t)


class FakeLLM:
    """Returns scripted model turns; records what it was sent."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list[types.Content]] = []

    async def generate(self, contents, config):
        self.seen.append(list(contents))
        parts = self.turns.pop(0)
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
        )


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def api_app():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    with S() as db:
        db.add_all([Student(student_no="1001", full_name="Ayse"), Student(student_no="1002", full_name="Mehmet")])
        db.flush()
        db.add_all([
            User(username="ayse", password_hash=hash_password("pw"), role="student", student_no="1001"),
            Tuition(student_no="1001", term="2026-FALL", amount=Decimal("45000"), currency="TRY",
                    paid_amount=Decimal("15000")),
            Tuition(student_no="1002", term="2026-FALL", amount=Decimal("45000"), currency="TRY",
                    paid_amount=Decimal("0")),
        ])
        db.commit()
    app = create_app(with_lifespan=False)

    def _db():
        s = S()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return app


def run_agent(api_app, turns, questions, confirm=lambda d: True):
    async def go():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://gateway")
        tuition = TuitionBackend("http://gateway", http)
        await tuition.login("ayse", "pw")
        llm = FakeLLM(turns)
        async with PolicyBackend(mcp_server.mcp) as policies:
            agent = TuitionAgent(llm, policies, tuition, confirm)
            traces = [await agent.ask(q) for q in questions]
        await http.aclose()
        return traces, llm

    return asyncio.run(go())


def last_function_responses(llm):
    """Function responses the agent sent back to the LLM on its last call."""
    return [p.function_response for p in llm.seen[-1][-1].parts if p.function_response]


# ---------------------------------------------------------------- scenarios
def test_mcp_only_scenario(api_app):
    traces, llm = run_agent(api_app, [
        [call("search_tuition_policies", query="fall payment deadline")],
        [text("The Fall 2026 deadline is September 30, 2026 (Section 2.1 - Fall term).")],
    ], ["When is the payment deadline?"])
    t = traces[0]
    assert [s.tool for s in t.steps] == ["search_tuition_policies"]
    assert t.steps[0].channel == "MCP"
    sections = [m["section"] for m in t.steps[0].result["matches"]]
    assert "2.1" in sections or "8.1" in sections
    # tool result was returned to the LLM before the final answer
    assert last_function_responses(llm)[0].name == "search_tuition_policies"
    assert "Section 2.1" in t.final_answer


def test_rest_only_scenario(api_app):
    traces, _ = run_agent(api_app, [
        [call("query_tuition")],
        [text("Your balance is 30,000 TRY.")],
    ], ["What is my balance?"])
    step = traces[0].steps[0]
    assert step.channel == "REST via API Gateway"
    assert step.result["studentNo"] == "1001" and step.result["balance"] == 30000


def test_combined_scenario_parallel_calls(api_app):
    traces, llm = run_agent(api_app, [
        [call("query_tuition", term="2026-FALL"), call("search_tuition_policies", query="installment eligibility")],
        [text("Your balance is 30,000 TRY; you may request up to three installments (Section 3.1).")],
    ], ["Can I pay my outstanding balance in installments?"])
    tools = {s.tool: s for s in traces[0].steps}
    assert set(tools) == {"query_tuition", "search_tuition_policies"}
    assert tools["query_tuition"].result["balance"] == 30000
    assert any(m["section"] == "3.1" for m in tools["search_tuition_policies"].result["matches"])
    assert len(last_function_responses(llm)) == 2


def test_payment_confirmed_calls_api(api_app):
    confirmations = []
    traces, _ = run_agent(api_app, [
        [text("You want to pay 5,000 TRY for 2026-FALL. Confirm?")],
        [call("pay_tuition", term="2026-FALL", amount=5000)],
        [text("Paid. Remaining 25,000 TRY.")],
    ], ["Pay 5000 of my balance", "yes"], confirm=lambda d: confirmations.append(d) or True)
    assert traces[0].steps == []  # first turn only asks
    step = traces[1].steps[0]
    # Section 7.3: student, term, amount and current balance shown before confirmation
    assert confirmations == [{"studentNo": "1001", "term": "2026-FALL", "amount": 5000.0,
                              "currentBalance": 30000.0, "currency": "TRY"}]
    assert step.result["paymentStatus"] == "SUCCESS" and step.result["remainingBalance"] == 25000
    assert step.result["transactionId"].startswith("AI-")


def test_payment_declined_never_calls_api(api_app):
    traces, _ = run_agent(api_app, [
        [call("pay_tuition", term="2026-FALL", amount=5000)],
        [text("Cancelled, nothing was charged.")],
        [call("query_tuition", term="2026-FALL")],
        [text("Balance still 30,000.")],
    ], ["pay 5000 now", "what's my balance"], confirm=lambda d: False)
    assert traces[0].steps[0].result["status"] == "CANCELLED_BY_USER"
    assert traces[1].steps[0].result["balance"] == 30000  # unchanged


def test_payment_for_unknown_term_is_rejected_before_confirmation(api_app):
    asked = []
    traces, _ = run_agent(api_app, [
        [call("pay_tuition", term="2030-FALL", amount=100)],
        [text("There is no tuition for that term.")],
    ], ["pay 100 for 2030-FALL"], confirm=lambda d: asked.append(d) or True)
    assert asked == [] and traces[0].steps[0].result["status"] == "REJECTED"


def test_payment_exceeding_balance_is_rejected_by_api(api_app):
    traces, _ = run_agent(api_app, [
        [call("pay_tuition", term="2026-FALL", amount=999999)],
        [text("That exceeds your balance.")],
    ], ["pay 999999"])
    assert traces[0].steps[0].result["error"]["code"] == "AMOUNT_EXCEEDS_BALANCE"


def test_identity_comes_from_session_not_prompt(api_app):
    """Even if the prompt names student 1002, the tool only sees the logged-in student."""
    traces, _ = run_agent(api_app, [
        [call("query_tuition", studentNo="1002")],  # LLM tries to pass an extra arg
        [text("I can only show your own records.")],
    ], ["What is the balance of student 1002?"])
    assert traces[0].steps[0].result["studentNo"] == "1001"


def test_trace_format_contains_tool_input_result_and_answer(api_app):
    traces, _ = run_agent(api_app, [
        [call("search_tuition_policies", query="late fee")],
        [text("A 2% one-time late fee (Section 4.1).")],
    ], ["late fee?"])
    out = format_trace(traces[0])
    assert "search_tuition_policies" in out and "input" in out and "result" in out
    assert "FINAL ANSWER: A 2% one-time late fee (Section 4.1)." in out

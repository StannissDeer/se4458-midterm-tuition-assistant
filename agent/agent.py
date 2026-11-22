"""AI Tuition Assistant: Gemini function calling + MCP + REST (via API Gateway).

Workflow per user message:
  user question -> LLM picks tool(s) -> agent executes MCP and/or REST calls
  -> results returned to LLM -> grounded answer (with §section references).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from google.genai import types

from backends import PolicyBackend, TuitionBackend

SYSTEM_PROMPT = """You are the University AI Tuition Assistant for the student who is logged in.

Tools:
- search_tuition_policies (MCP): the University Tuition Policies document (UNI-TUITION-2026-01):
  deadlines, installments, late fees, scholarships, refunds, banking/payment rules. It contains NO
  student-specific balances, transactions or approval records.
- query_tuition (REST): the logged-in student's LIVE tuition total, paid amount and balance.
- pay_tuition (REST): records a simulated payment for the logged-in student.

Rules:
1. Choose tools by the question: policy questions -> search_tuition_policies; questions about the
   student's own amounts -> query_tuition; questions combining the student's balance with a rule
   (e.g. installment eligibility) -> call BOTH and combine them. Always call query_tuition again
   for every question that depends on the student's amounts; never reuse amounts from earlier
   turns, because balances change (Section 2.3).
2. Never invent amounts, dates or rules. Base every fact on tool results. When you use policy text,
   cite the section ID and heading as returned, e.g. "(Section 3.1 - Eligibility)".
3. Policy eligibility is not approval: never promise that an installment plan, scholarship,
   exception or refund is approved (Sections 3.1, 3.4, 4.3, 5.2). Say the student may request it.
4. You can only see the logged-in student's records. If the user asks about another student number,
   refuse politely - the tools always use the authenticated identity.
5. Payments (Section 7.3): a question like "Can I pay $500?" is NOT authorization. Before paying,
   show the student number, term, amount and current balance (call query_tuition first) and ask
   the user to confirm. Call pay_tuition only after the user explicitly confirms. The system will
   ask for a final confirmation too. Never claim a payment succeeded unless the tool returned
   paymentStatus SUCCESS. If the user does not say how much or for which term, ask.
6. Terms look like 2026-FALL, 2027-SPRING.
7. Reply in the same language as the user (Turkish or English), concisely."""

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="search_tuition_policies",
        description="Search the indexed University Tuition Policies document and return relevant "
        "excerpts with section references. Use for rules, deadlines, installments, fees, refunds, "
        "scholarships, bank transfer instructions.",
        parameters_json_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query in English keywords"}},
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="query_tuition",
        description="Get the logged-in student's live tuition total, paid amount and remaining "
        "balance. Omit term for the overall balance across all terms (per-term breakdown included).",
        parameters_json_schema={
            "type": "object",
            "properties": {"term": {"type": "string", "description": "Optional, e.g. 2026-FALL"}},
        },
    ),
    types.FunctionDeclaration(
        name="pay_tuition",
        description="Record a simulated partial or full tuition payment for the logged-in student. "
        "Only call after the user explicitly confirmed the term and amount.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "e.g. 2026-FALL"},
                "amount": {"type": "number", "description": "Amount to pay, > 0"},
            },
            "required": ["term", "amount"],
        },
    ),
]

ConfirmFn = Callable[[dict], bool | Awaitable[bool]]


@dataclass
class TraceStep:
    step: int
    tool: str
    channel: str  # "MCP" or "REST via API Gateway"
    input: dict
    result: Any


@dataclass
class TurnTrace:
    question: str
    steps: list[TraceStep] = field(default_factory=list)
    final_answer: str = ""

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "tool_calls": [s.__dict__ for s in self.steps],
            "final_answer": self.final_answer,
        }


class GeminiLLM:
    """Thin wrapper so tests can substitute a scripted fake LLM.
    If the main model is overloaded (429/500/503), it retries and then falls back to the next
    model in GEMINI_FALLBACK_MODELS."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from google import genai

        self.client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        fallbacks = os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite")
        self.models = [self.model] + [m.strip() for m in fallbacks.split(",")
                                      if m.strip() and m.strip() != self.model]

    async def generate(self, contents: list[types.Content], config: types.GenerateContentConfig):
        last_error = None
        for model in self.models:
            delay = 2.0
            for attempt in range(3):
                try:
                    return await self.client.aio.models.generate_content(
                        model=model, contents=contents, config=config
                    )
                except Exception as e:
                    if getattr(e, "code", None) not in (429, 500, 503):
                        raise
                    last_error = e
                    if attempt < 2:
                        await asyncio.sleep(delay)
                        delay *= 2
            print(f"[llm] {model} is unavailable, trying the next model...")
        raise last_error


class TuitionAgent:
    MAX_TOOL_ROUNDS = 6

    def __init__(self, llm, policies: PolicyBackend, tuition: TuitionBackend, confirm: ConfirmFn):
        self.llm, self.policies, self.tuition, self.confirm = llm, policies, tuition, confirm
        self.history: list[types.Content] = []
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )

    # ------------------------------------------------------------------ tools
    async def _execute(self, name: str, args: dict) -> tuple[str, Any]:
        if name == "search_tuition_policies":
            return "MCP", await self.policies.search(str(args.get("query", "")))
        if name == "query_tuition":
            return "REST via API Gateway", await self.tuition.query_tuition(args.get("term") or None)
        if name == "pay_tuition":
            return "REST via API Gateway", await self._pay(args)
        return "-", {"error": f"Unknown tool {name}"}

    async def _pay(self, args: dict) -> dict:
        try:
            amount = round(float(args.get("amount")), 2)
        except (TypeError, ValueError):
            return {"status": "REJECTED", "reason": "amount must be a number"}
        term = str(args.get("term", ""))
        # Section 7.3: show student, term, amount AND current balance before confirmation.
        current = await self.tuition.query_tuition(term)
        if "balance" not in current:
            return {"status": "REJECTED", "reason": "Could not read the current balance for this term",
                    "details": current}
        details = {"studentNo": self.tuition.student_no, "term": term, "amount": amount,
                   "currentBalance": current["balance"], "currency": current.get("currency")}
        # Hard gate in code: the payment API is never called without an explicit human "yes",
        # no matter what the LLM decided.
        ok = self.confirm(details)
        if inspect.isawaitable(ok):
            ok = await ok
        if not ok:
            return {"status": "CANCELLED_BY_USER", "message": "User did not confirm. No payment was made."}
        txn = f"AI-{uuid.uuid4().hex[:12].upper()}"
        return await self.tuition.pay_tuition(term, amount, txn)

    # ------------------------------------------------------------------ loop
    async def ask(self, question: str) -> TurnTrace:
        trace = TurnTrace(question)
        self.history.append(types.Content(role="user", parts=[types.Part(text=question)]))
        n = 0
        for _ in range(self.MAX_TOOL_ROUNDS):
            resp = await self.llm.generate(self.history, self.config)
            content = resp.candidates[0].content if resp.candidates else None
            if content is None:
                trace.final_answer = "(empty response from LLM)"
                return trace
            self.history.append(content)  # keeps thought signatures for the next round
            calls = [p.function_call for p in (content.parts or []) if p.function_call]
            if not calls:
                trace.final_answer = "".join(p.text or "" for p in content.parts if not p.thought).strip()
                return trace
            parts = []
            for call in calls:
                n += 1
                args = dict(call.args or {})
                channel, result = await self._execute(call.name, args)
                trace.steps.append(TraceStep(n, call.name, channel, args, result))
                parts.append(types.Part.from_function_response(name=call.name, response={"result": result}))
            self.history.append(types.Content(role="user", parts=parts))
        trace.final_answer = "(stopped: too many tool rounds)"
        return trace


def format_trace(trace: TurnTrace) -> str:
    lines = ["─" * 70, f"TOOL TRACE  |  Q: {trace.question}"]
    if not trace.steps:
        lines.append("  (no tool calls)")
    for s in trace.steps:
        res = json.dumps(s.result, ensure_ascii=False, default=str)
        lines.append(f"  [{s.step}] {s.tool}  ({s.channel})")
        lines.append(f"      input : {json.dumps(s.input, ensure_ascii=False)}")
        lines.append(f"      result: {res[:600]}{'…' if len(res) > 600 else ''}")
    lines.append(f"  FINAL ANSWER: {trace.final_answer}")
    lines.append("─" * 70)
    return "\n".join(lines)


def save_trace(trace: TurnTrace, folder: str = "logs") -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"trace-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace.to_dict(), f, indent=2, ensure_ascii=False, default=str)
    return path

"""Command-line chat for the AI Tuition Assistant.

  export GEMINI_API_KEY=...            # Google AI Studio key
  export GATEWAY_URL=http://localhost:8080   # Kong (all REST traffic goes through it)
  export MCP_URL=http://localhost:8100/mcp
  python chat.py --user ayse --password ayse123
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent import GeminiLLM, TuitionAgent, format_trace, save_trace  # noqa: E402
from backends import PolicyBackend, TuitionBackend  # noqa: E402


def cli_confirm(details: dict) -> bool:
    print("\n⚠️  PAYMENT CONFIRMATION REQUIRED")
    cur = details.get("currency") or ""
    print(f"    Student: {details['studentNo']}   Term: {details['term']}")
    print(f"    Current balance: {details['currentBalance']} {cur}   Amount to pay: {details['amount']} {cur}")
    ans = input("    Type 'yes' (or 'evet') to confirm, anything else to cancel: ").strip().lower()
    return ans in ("yes", "evet", "y", "e")


async def main():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://localhost:8080"))
    ap.add_argument("--mcp", default=os.getenv("MCP_URL", "http://localhost:8100/mcp"))
    ap.add_argument("--no-trace", action="store_true")
    a = ap.parse_args()

    tuition = TuitionBackend(a.gateway)
    info = await tuition.login(a.user, a.password)
    print(f"Logged in as {a.user} (role={info['role']}, studentNo={info.get('student_no')})")
    print(f"REST gateway: {a.gateway}   MCP: {a.mcp}   Type 'exit' to quit.\n")

    async with PolicyBackend(a.mcp) as policies:
        agent = TuitionAgent(GeminiLLM(), policies, tuition, cli_confirm)
        while True:
            try:
                q = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("exit", "quit", "çık"):
                break
            if not q:
                continue
            try:
                trace = await agent.ask(q)
            except Exception as e:  # keep the chat alive on LLM/API errors
                agent.history.clear()
                print(f"\n[error] {type(e).__name__}: {str(e)[:300]}\n")
                continue
            if not a.no_trace:
                print(format_trace(trace))
                print(f"(trace saved to {save_trace(trace)})")
            print(f"\nAssistant: {trace.final_answer}\n")


if __name__ == "__main__":
    asyncio.run(main())

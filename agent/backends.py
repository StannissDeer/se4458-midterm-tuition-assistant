"""Tool executors. The LLM never talks to these services; the agent does.

* PolicyBackend  -> MCP client -> MCP server (search_tuition_policies). NOT via the API Gateway.
* TuitionBackend -> HTTP       -> API Gateway -> Tuition REST API (JWT of the logged-in user).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from mcp import Client


class PolicyBackend:
    def __init__(self, server: Any):
        """server: MCP URL (e.g. http://localhost:8100/mcp) or an in-process MCPServer (tests)."""
        self._server = server
        self._client: Client | None = None

    async def __aenter__(self):
        self._client = Client(self._server)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.__aexit__(*exc)

    async def search(self, query: str, top_k: int = 4) -> dict:
        assert self._client, "PolicyBackend must be used as an async context manager"
        res = await self._client.call_tool("search_tuition_policies", {"query": query, "top_k": top_k})
        if res.structured_content:
            data = res.structured_content
            # some SDK versions wrap non-object returns as {"result": ...}
            return data.get("result", data) if isinstance(data, dict) else {"result": data}
        text = "".join(getattr(c, "text", "") for c in res.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"query": query, "raw": text, "is_error": res.is_error}


class TuitionBackend:
    """Calls the Tuition API *through the gateway* with the logged-in user's JWT.
    The student number always comes from the authenticated session, never from the prompt."""

    def __init__(self, gateway_url: str, http: httpx.AsyncClient | None = None):
        self.base = gateway_url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=20)
        self.token: str | None = None
        self.student_no: str | None = None
        self.role: str | None = None

    async def login(self, username: str, password: str) -> dict:
        r = await self.http.post(f"{self.base}/api/v1/auth/login",
                                 json={"username": username, "password": password})
        data = r.json()
        if r.status_code != 200:
            raise PermissionError(data.get("error", {}).get("message", "Login failed"))
        self.token, self.student_no, self.role = data["access_token"], data.get("student_no"), data["role"]
        return data

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @staticmethod
    def _result(r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text}
        return body if r.is_success else {"httpStatus": r.status_code, **body}

    async def query_tuition(self, term: str | None = None) -> dict:
        if not self.student_no:
            return {"error": {"code": "NOT_A_STUDENT", "message": "Logged-in user has no student number"}}
        params = {"term": term} if term else None
        r = await self.http.get(f"{self.base}/api/v1/banking/tuition/{self.student_no}",
                                params=params, headers=self._headers())
        return self._result(r)

    async def pay_tuition(self, term: str, amount: float, transaction_id: str) -> dict:
        r = await self.http.post(
            f"{self.base}/api/v1/banking/tuition/payments",
            json={"studentNo": self.student_no, "term": term, "amount": amount,
                  "transactionId": transaction_id},
            headers=self._headers(),
        )
        return self._result(r)

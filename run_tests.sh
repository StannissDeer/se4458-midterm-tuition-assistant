#!/usr/bin/env bash
# Runs all unit tests. No cloud, gateway, database server or LLM key required.
# Optional: TEST_DATABASE_URL=postgresql+psycopg2://user:pw@host/db ./run_tests.sh  (API tests on PostgreSQL)
set -e
cd "$(dirname "$0")"
echo "== REST API =="     && (cd api && python -m pytest -v tests)
echo "== MCP server =="   && (cd mcp_server && python -m pytest -v tests)
echo "== AI Agent =="     && (cd agent && python -m pytest -v tests)

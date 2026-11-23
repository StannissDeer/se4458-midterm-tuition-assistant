# SE 4458 Midterm 1 — Group 1: AI Tuition Assistant

A conversational assistant for students asking about tuition deadlines, installment eligibility, payment rules, balances and payments. A Gemini LLM picks the tools through real function calling. The **AI Agent** then runs those calls:

- **MCP Server**: indexed search over `University_Tuition_Policies.md`, for policy questions.
- **Tuition REST API through the Kong API Gateway**: live balances and simulated payments.

> **Live deployment (Azure):**
> - API Gateway: https://tuition-gateway.delightfulforest-2c0bb7e1.polandcentral.azurecontainerapps.io (Swagger: `/docs`)
> - MCP server: https://tuition-mcp.delightfulforest-2c0bb7e1.polandcentral.azurecontainerapps.io/mcp
> - Demo video: https://drive.google.com/file/d/1NbTcMxXFx8qkR9Zu_D123nDn8RLWA1vM/view?usp=sharing

---

## 1. Architecture

```mermaid
flowchart LR
    U[Student<br/>CLI chat] --> A[AI Agent<br/>agent/]
    A <-->|function calling| L[Gemini LLM]
    A -->|MCP client<br/>Streamable HTTP| M[MCP Server<br/>search_tuition_policies]
    M --> I[(BM25 index<br/>index.json)]
    A -->|REST + JWT| G[Kong API Gateway<br/>/api/v1/*]
    MA[Mobile App] --> G
    BA[Banking App] --> G
    AD[Admin Web] --> G
    G --> R[Tuition REST API<br/>FastAPI]
    R --> D[(PostgreSQL)]
```

- The LLM **never** calls backend services. It returns `function_call`s, the agent runs them, and the results go back to the LLM as `function_response`s.
- The MCP path (agent → MCP server) is separate from the API Gateway path (agent → Kong → API).
- The policy document is **indexed once**: at image build or server start-up, saved to `index.json`. It is never re-indexed per question.

| Folder | Content |
|---|---|
| `api/` | FastAPI REST service, SQLAlchemy models, JWT auth, unit tests |
| `mcp_server/` | MCP SDK server, chunker and BM25 indexer, policy document, tests |
| `agent/` | Gemini agent, MCP client, gateway REST client, CLI chat, tests with a fake LLM |
| `gateway/` | `kong.yml` (declarative Kong config: routes, JWT, per-student rate limit, gateway secret) and the cloud `Dockerfile` |
| `docs/` | OpenAPI spec, ER diagram, MCP tool schema, test results, screenshots, tool traces |
| `test-data/` | Sample CSV for batch upload |

**Technology:** Python 3.12+, FastAPI, SQLAlchemy 2, PostgreSQL 16, Kong 3.9 (DB-less), MCP Python SDK v2 (`MCPServer`), `google-genai` (Gemini), pytest.

---

## 2. Running locally

### 2.1 Backend stack (PostgreSQL + API + MCP + Kong)
```bash
docker compose up --build
```
| Service | URL |
|---|---|
| **API Gateway (all REST traffic)** | http://localhost:8080 |
| Swagger UI (through the gateway) | http://localhost:8080/docs |
| Kong admin (route list) | http://localhost:8001/routes |
| MCP server | http://localhost:8100/mcp |

The API container is not published on the host, so REST clients must go through Kong. On start-up the API creates the tables and loads demo data (`python -m app.seed`).

### 2.2 AI Agent (CLI chat)
```bash
pip install -r requirements-dev.txt
export GEMINI_API_KEY=...                  # free key from Google AI Studio
export GEMINI_MODEL=gemini-3.5-flash    # optional
cd agent
python chat.py --user ayse --password ayse123 --gateway http://localhost:8080 --mcp http://localhost:8100/mcp
```

### 2.3 Without Docker
```bash
# PostgreSQL running locally with db/user/password = tuition
cd api && python -m app.seed && uvicorn app.main:app --port 8000
cd mcp_server && python indexer.py && python server.py
# then run the agent with --gateway http://localhost:8000 (direct; the gateway is bypassed, use only for development)
```

### 2.4 Test data
| User | Password | Role | Student No | Seeded tuition |
|---|---|---|---|---|
| `ayse` | `ayse123` | student | 2021001 | 2026-FALL $3,000 ($1,000 paid → balance $2,000, installment threshold met) · 2027-SPRING $3,000 |
| `mehmet` | `mehmet123` | student | 2021002 | 2026-FALL $3,000 (fully paid) |
| `zeynep` | `zeynep123` | student | 2021003 | 2026-FALL $3,000 ($2,800 paid → balance $200, below the $300 installment threshold) |
| `can` | `can123` | student | 2021004 | 2026-FALL $2,250 (unpaid) |
| `admin` | `admin123` | admin | – | – |

Batch CSV: `test-data/tuition_batch.csv`. It contains 3 valid rows and 2 rows that fail on purpose.

---

## 3. REST API (v1)

Full contract: Swagger UI at `/docs` and `docs/openapi.json`.

| Client | Operation | Method & route | Auth | Paging |
|---|---|---|---|---|
| – | Login (get JWT) | `POST /api/v1/auth/login` | – | – |
| Mobile App | Query Tuition | `GET /api/v1/mobile/tuition/{studentNo}?term=` | **No** (3 calls/student/day) | No |
| Banking App | Query Tuition | `GET /api/v1/banking/tuition/{studentNo}?term=` | **Yes** | No |
| Banking App | Pay Tuition | `POST /api/v1/banking/tuition/payments` | **Yes** | No |
| Admin | Add Tuition | `POST /api/v1/admin/tuition` | **Yes** (admin) | No |
| Admin | Add Tuition – Batch | `POST /api/v1/admin/tuition/batch` (multipart CSV) | **Yes** (admin) | No |
| Admin | Unpaid Tuition Status | `GET /api/v1/admin/tuition/unpaid?term=&page=&pageSize=` | **Yes** (admin) | **Yes** |

**Term or overall balance (API contract):**
- With `?term=2026-FALL`, the API returns that term only (`scope: "TERM"`).
- Without `term`, it returns the **overall** totals across all terms (`scope: "OVERALL"`) plus a per-term breakdown.

**Example:** Pay Tuition

Request:
```http
POST /api/v1/banking/tuition/payments
Authorization: Bearer <jwt>
{"studentNo":"2021001","term":"2026-FALL","amount":500,"transactionId":"BANK-001"}
```
Response:
```json
201 {"paymentStatus":"SUCCESS","transactionId":"BANK-001","studentNo":"2021001","term":"2026-FALL",
     "paidAmount":500.0,"totalPaid":1500.0,"remainingBalance":1500.0,"currency":"USD"}
```

**Error format** (all API errors):
```json
409 {"error":{"status":409,"code":"DUPLICATE_TRANSACTION","message":"Transaction BANK-001 was already processed"}}
```

| Code | HTTP | When |
|---|---|---|
| `UNAUTHORIZED`, `INVALID_TOKEN`, `TOKEN_EXPIRED` | 401 | Token missing, bad or expired |
| `FORBIDDEN` | 403 | Another student's record, or not an admin |
| `STUDENT_NOT_FOUND`, `TUITION_NOT_FOUND` | 404 | Unknown student, or no tuition for the term |
| `DUPLICATE_TRANSACTION`, `ALREADY_PAID`, `AMOUNT_BELOW_PAID`, `CURRENCY_LOCKED` | 409 | Conflicts |
| `AMOUNT_EXCEEDS_BALANCE`, `VALIDATION_ERROR` | 422 | Bad amounts or input |
| `INVALID_TERM`, `INVALID_CSV`, `INVALID_PAGING` | 400 | Bad parameters |
| `RATE_LIMIT_EXCEEDED` | 429 | 4th mobile query by the same student on the same day |

**Versioning:** all routes live under `/api/v1` (the FastAPI router prefix and the Kong routes). A breaking change is shipped as `/api/v2`, with a new router and new gateway routes, while v1 keeps running.

---

## 4. API Gateway (Kong)

Config file: `gateway/kong.yml`. All REST operations go through it, including the AI Agent's calls.

| Public route (gateway) | Upstream | Plugins |
|---|---|---|
| `POST /api/v1/auth/*` | `tuition-api-v1` | global only |
| `GET /api/v1/mobile/tuition/{studentNo}` | `tuition-api-v1` | `pre-function` + `rate-limiting` (day = 3, **keyed by student number**) |
| `GET,POST /api/v1/banking/*` | `tuition-api-v1` | **`jwt`** (protected) |
| `GET,POST /api/v1/admin/*` | `tuition-api-v1` | **`jwt`** (protected). The API also checks `role=admin` |
| `GET /docs`, `/openapi.json`, `/health` | `tuition-api-v1` | – |
| *(global)* | | `rate-limiting` 120/min per IP, `correlation-id` |

**Per-student mobile limit:**
- The route regex captures `studentNo` from the URL. A `pre-function` writes it into the `X-Student-No` header, overwriting anything the client sent. `rate-limiting` then uses `limit_by: header`.
- Result: the limit applies **per student identity, not per client IP**.
- The API enforces the same rule in the database (`mobile_query_usage` table), so the limit still holds if the gateway restarts or runs as several replicas with local counters.

**Gateway-only access:** Kong adds a shared secret header (`X-Gateway-Secret`) to every upstream request, after removing any copy sent by the client. The API rejects any request without the correct value (403 `GATEWAY_REQUIRED`). So even where the API's URL is reachable (as in the Azure deployment), it can only be used through the gateway.

**JWT:** the API signs HS256 tokens with `iss = tuition-auth`. The Kong consumer `tuition-auth-service` has a credential with `key = tuition-auth`, so Kong validates the signature and `exp` before forwarding. The API validates the token again (defence in depth) and authorizes the request: matching student number, or admin role.

**Gateway demo commands:**
```bash
GW=http://localhost:8080
# 1) public routed endpoint
curl -i $GW/api/v1/mobile/tuition/2021001?term=2026-FALL
# 2) protected endpoint without a token -> gateway error response
curl -i $GW/api/v1/banking/tuition/2021001
#    HTTP/1.1 401 Unauthorized   {"message":"Unauthorized"}
# 3) protected endpoint with a token
TOKEN=$(curl -s -X POST $GW/api/v1/auth/login -H 'content-type: application/json' \
        -d '{"username":"ayse","password":"ayse123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -i -H "Authorization: Bearer $TOKEN" $GW/api/v1/banking/tuition/2021001
# 4) rate limit: the 4th call for the same student on the same day
for i in 1 2 3 4; do curl -s -o /dev/null -w "%{http_code}\n" $GW/api/v1/mobile/tuition/2021004; done
#    200 200 200 429  -> {"message":"Mobile tuition query limit (3 per student per day) exceeded"}
# route mappings
curl -s localhost:8001/routes | python -m json.tool
```

---

## 5. MCP Server and indexing

- **SDK:** official MCP Python SDK v2 (`mcp.server.mcpserver.MCPServer`), Streamable HTTP transport at `/mcp`. Run with `MCP_TRANSPORT=stdio` for stdio.
- **Tool:** `search_tuition_policies(query: string, top_k: int = 4)`. Following Section 9 of the policy, it returns structured content `{document, documentId, query, matches:[{section, heading, parentSection, score, text}], message}`, e.g. `section: "3.2"`, `heading: "Number and size of installments"`, `documentId: "UNI-TUITION-2026-01"`. Full schema: `docs/mcp_tool_schema.json`.
- **Chunking:** one chunk per `###` subsection, tagged with its `##` parent; a `##` section without subsections (1 and 9) is one chunk. The document gives 25 chunks, each with a section ID and heading (e.g. `3.1 Eligibility`).
- **Index:** BM25 full-text search.
  - Tokenization: lowercase, stop-word removal, light suffix stemming, and a small synonym map (e.g. *deadline ↔ due*, *IBAN → bank transfer*).
  - Section titles are weighted twice.
- **Index lifecycle:**
  - `python indexer.py` builds `index.json`. The Docker image runs it at build time.
  - On start-up the server loads the index and rebuilds it only if the document's SHA-256 changed.
  - Queries only read the index.
- Embeddings and vector search are not used (optional per the assignment).

**Three distinct retrieval tests** (`mcp_server/tests/test_mcp.py`, plus the demo):

| Query | Top result |
|---|---|
| "When is the Fall payment deadline?" | 8.1 When is the Fall tuition deadline? / 2.1 Fall term |
| "Can I pay my outstanding balance in installments?" | 8.2 Can I pay my balance in three installments? / 3.1 Eligibility |
| "late fee for overdue payment" | 4.1 Late fee rule |
| "refund if I withdraw" (extra) | 6.1 Early withdrawal |

---

## 6. AI Agent

- **LLM:** Google Gemini (`google-genai`) with **native function calling**. Automatic function calling is disabled, so the agent runs every call itself. There is no keyword routing.
- **Tools declared to the LLM:**

| Tool | Path | Use |
|---|---|---|
| `search_tuition_policies(query)` | MCP | Policy questions |
| `query_tuition(term?)` | REST via gateway | Live balance |
| `pay_tuition(term, amount)` | REST via gateway | Simulated payment |

- **Loop:**
  1. The user message goes to Gemini.
  2. Gemini returns `function_call`(s), sometimes several in parallel.
  3. The agent runs them and appends the `function_response`s.
  4. This repeats until Gemini returns text (at most 6 rounds).
  5. The final answer cites policy sections, e.g. *(Section 3.1 - Eligibility)*. Following Sections 3.4 and 5.2, the agent never promises that an installment plan or scholarship is approved.
- **Identity:**
  - The agent logs in and calls the API with **the logged-in user's JWT**.
  - The tools take **no student number**: it comes from the session. A prompt like "show student 2021002's balance" cannot reach another student's data.
  - The API also returns 403 for a mismatching number.
- **Payment confirmation:**
  - Following Section 7.3, the LLM must state the student, term, amount and current balance and ask for confirmation first. "Can I pay $500?" is not treated as authorization.
  - The code also has a **hard gate**: when `pay_tuition` is called, the agent first reads the current balance for that term, then the CLI shows student, term, current balance and amount and requires `yes`/`evet` before it calls the payment API. If the user declines, the API is never called and the LLM receives `CANCELLED_BY_USER`. A term with no tuition record is rejected before any confirmation is asked.
  - Transaction IDs are generated by the agent (`AI-xxxxxxxxxxxx`).
- **Tool trace:** each turn prints the tool name, channel (MCP or REST), input, result and final answer, and saves them as JSON in `agent/logs/`.

**Demo scenarios** (screenshots and logs go in `docs/screenshots/`):

| # | Question | Expected tools |
|---|---|---|
| 1 | "When is the payment deadline?" | **MCP only**: `search_tuition_policies` |
| 2 | "What is my balance?" | **REST only**: `query_tuition` |
| 3 | "Can I pay my outstanding balance in installments?" | **Combined**: `query_tuition` + `search_tuition_policies` |
| 4 | "Pay $500 of my 2026-FALL balance" → "yes" | `pay_tuition`, only after explicit confirmation |

---

## 6.1 Evidence (screenshots and traces)

| # | What it shows | File |
|---|---|---|
| 1 | Agent, MCP only (deadline question) | `docs/screenshots/01-mcp-only.png` |
| 2 | Agent, REST only (balance via Kong) | `docs/screenshots/02-rest-only.png` |
| 3 | Agent, MCP + REST combined (installment eligibility) | `docs/screenshots/03-combined.png` |
| 4 | Agent, payment with explicit confirmation | `docs/screenshots/04-payment-confirm.png` |
| 5 | Gateway: protected route, 401 without token / 200 with token | `docs/screenshots/07-gateway-401.png` |
| 6 | Swagger UI through the gateway | `docs/screenshots/10-swagger.png` |
| 7 | Three distinct MCP retrieval queries over the MCP protocol | `docs/screenshots/11-mcp-queries.png` |
| 8 | Unit tests (taken before the 2 gateway-secret tests were added; current total is 64) | `docs/screenshots/12-tests.png` |
| 9 | **Azure:** agent running fully in the cloud (Kong + API + PostgreSQL + MCP), combined scenario | `docs/screenshots/13-cloud-agent.png`, `docs/screenshots/13-cloud-agent-2.png` |
| 10 | **Azure:** gateway 401 without token, direct API call blocked (403 `GATEWAY_REQUIRED`), per-student limit 200/200/200/429 | `docs/screenshots/15-cloud-gateway.png` |

JSON tool traces of the demo conversations: `docs/traces/` (`1-mcp-only`, `2-rest-only`, `3-combined`, `4a`/`4b` payment, `5-cloud-combined` on Azure).

---

## 7. Authentication and authorization
- **Login:** `POST /api/v1/auth/login` returns an HS256 JWT with claims `iss`, `sub`, `role`, `student_no` and `exp` (60 min). Passwords are stored as PBKDF2-SHA256 hashes.
- **Students** can query and pay **only their own** records. Admins can query any record but cannot pay for students. Only admins can add tuition or see unpaid lists.
- **Two layers:** Kong checks the token's signature and expiry. The API re-checks the token and authorizes the action.

---

## 8. Unit tests

```bash
pip install -r requirements-dev.txt
./run_tests.sh
# optional: run the API tests on PostgreSQL instead of in-memory SQLite
TEST_DATABASE_URL=postgresql+psycopg2://tuition:tuition@localhost:5432/tuition_test ./run_tests.sh
```

| Suite | Tests | Covers |
|---|---|---|
| `api/tests` | 45 | Every operation, success and failure cases: auth, mobile limit per student, term vs overall, 401/403/expired/tampered tokens, partial and full payment, duplicate transaction, overpayment, upsert rules, CSV partial failures, paging |
| `mcp_server/tests` | 10 | Chunking, document ID, index save and reload, 4 distinct retrieval queries, MCP protocol `list_tools` / `call_tool` with the Section 9 response shape, empty query |
| `agent/tests` | 9 | MCP-only, REST-only and combined scenarios; payment confirmed (with current balance shown) or declined; unknown term; overpayment; identity from session; trace format. Uses a **scripted fake LLM**, so no API key is needed |

- None of the tests need the cloud, the gateway or a live LLM.
- The API tests use in-memory SQLite **for test isolation only**; the application itself runs on PostgreSQL. The same suite also passes on PostgreSQL via `TEST_DATABASE_URL`.
- Latest run: `docs/test-results.txt` (**64 passed**).

---

## 9. Cloud deployment (Azure)

**Live URLs**
- API Gateway: `https://tuition-gateway.delightfulforest-2c0bb7e1.polandcentral.azurecontainerapps.io` (Swagger: `/docs`)
- MCP server: `https://tuition-mcp.delightfulforest-2c0bb7e1.polandcentral.azurecontainerapps.io/mcp`

| Component | Azure resource | Region |
|---|---|---|
| PostgreSQL 16 | Azure Database for PostgreSQL – Flexible Server, Burstable B1ms (`tuition-db-*`) | Poland Central |
| Container registry | Azure Container Registry, Basic (`tuitionacr*`) | Poland Central |
| Tuition API | Container App `tuition-api` (`api/Dockerfile`). Only accepts requests carrying the gateway secret | Poland Central |
| API Gateway | Container App `tuition-gateway` (Kong, `gateway/Dockerfile`), external ingress | Poland Central |
| MCP server | Container App `tuition-mcp` (`mcp_server/Dockerfile`), external ingress | Poland Central |

`gateway/Dockerfile` writes `API_URL`, `JWT_SECRET` and `GATEWAY_SECRET` from environment variables into `kong.yml` when the container starts. Secrets are passed as Container App environment variables and are not stored in the repository.

**Deployment steps** (PowerShell, from the repository root; secrets generated locally and kept out of Git):
```powershell
az login
az extension add --name containerapp --upgrade
$RG="se4458-rg"; $LOC="polandcentral"; $ENV="tuition-env"; $PG="tuition-db-<n>"; $ACR="tuitionacr<n>"
$PGPASS="<alphanumeric>"; $JWT="<alphanumeric>"; $GWSECRET="<alphanumeric>"

az group create -n $RG -l $LOC
az postgres flexible-server create -g $RG -n $PG -l $LOC --tier Burstable --sku-name Standard_B1ms --storage-size 32 `
  --version 16 --admin-user tuition --admin-password $PGPASS --public-access 0.0.0.0 --yes
az postgres flexible-server db create -g $RG --server-name $PG --name tuition
az containerapp env create -g $RG -n $ENV -l $LOC
az acr create -g $RG -n $ACR -l $LOC --sku Basic --admin-enabled true
az acr login -n $ACR
$ACRUSER = az acr credential show -n $ACR --query username -o tsv
$ACRPASS = az acr credential show -n $ACR --query "passwords[0].value" -o tsv

# API
docker build -t "$ACR.azurecr.io/tuition-api:v2" ./api; docker push "$ACR.azurecr.io/tuition-api:v2"
az containerapp create -g $RG -n tuition-api --environment $ENV --image "$ACR.azurecr.io/tuition-api:v2" `
  --registry-server "$ACR.azurecr.io" --registry-username $ACRUSER --registry-password $ACRPASS `
  --ingress external --target-port 8000 `
  --env-vars "DATABASE_URL=postgresql+psycopg2://tuition:$PGPASS@$PG.postgres.database.azure.com:5432/tuition?sslmode=require" `
             "JWT_SECRET=$JWT" "JWT_ISSUER=tuition-auth" "GATEWAY_SECRET=$GWSECRET"
$API_FQDN = az containerapp show -g $RG -n tuition-api --query properties.configuration.ingress.fqdn -o tsv

# Gateway
docker build -t "$ACR.azurecr.io/tuition-gateway:v2" ./gateway; docker push "$ACR.azurecr.io/tuition-gateway:v2"
az containerapp create -g $RG -n tuition-gateway --environment $ENV --image "$ACR.azurecr.io/tuition-gateway:v2" `
  --registry-server "$ACR.azurecr.io" --registry-username $ACRUSER --registry-password $ACRPASS `
  --ingress external --target-port 8000 --env-vars "API_URL=https://$API_FQDN" "JWT_SECRET=$JWT" "GATEWAY_SECRET=$GWSECRET"

# MCP
docker build -t "$ACR.azurecr.io/tuition-mcp:v1" ./mcp_server; docker push "$ACR.azurecr.io/tuition-mcp:v1"
az containerapp create -g $RG -n tuition-mcp --environment $ENV --image "$ACR.azurecr.io/tuition-mcp:v1" `
  --registry-server "$ACR.azurecr.io" --registry-username $ACRUSER --registry-password $ACRPASS `
  --ingress external --target-port 8100
```

Notes:
- PostgreSQL was not allowed in `westeurope` on the Azure for Students subscription, so everything runs in `polandcentral`.
- The environment is an "express" Container Apps environment, where internal ingress was not reachable from the gateway. The API therefore has external ingress, but it is locked to the gateway by the shared secret header (direct calls get 403).
- Apps scale to zero when idle, so the first request after a pause can take 10–30 s.

Agent against the cloud:
`python chat.py --user ayse --password ayse123 --gateway https://tuition-gateway.delightfulforest-2c0bb7e1.polandcentral.azurecontainerapps.io --mcp https://tuition-mcp.delightfulforest-2c0bb7e1.polandcentral.azurecontainerapps.io/mcp`

**Cost and clean-up:** about $15–20/month, covered by Azure for Students credit. After grading: `az group delete -n se4458-rg --yes`.

---

## 10. LLM cost
- Gemini **free tier** (Google AI Studio key), model `gemini-3.5-flash` (configurable with `GEMINI_MODEL`). A demo session uses only a few thousand tokens per question, which costs $0 on the free tier.
- Free-tier 429/503 errors are retried with exponential backoff; if the main model stays unavailable the agent falls back to `GEMINI_FALLBACK_MODELS` (default `gemini-3.5-flash-lite,gemini-3.1-flash-lite`).
- Every test uses a fake LLM, so grading the tests costs nothing.

---

## 11. Assumptions
- **Policy document:** `University_Tuition_Policies.md` (UNI-TUITION-2026-01) is the instructor-provided document, unchanged. Section 9 (AI/MCP guidance) is indexed too, because it is part of the document.
- **Currency of demo data:** USD, because the policy's amounts (e.g. the $300 installment threshold) are in dollars. The API also accepts TRY and EUR.
- **Installments:** the API does not create installment plans. The agent explains the policy (Sections 3.1–3.4) next to the live balance; a partial payment is not an installment approval.
- **Policy vs database:** the policy document holds rules only. Balances come only from the database through the API.
- **Terms:** format `YYYY-FALL|SPRING|SUMMER`.
- **Currencies:** `TRY`, `USD`, `EUR`. If an overall query covers terms in different currencies, `currency` is `MIXED` and the per-term breakdown should be used.
- **Money:** stored as `NUMERIC(12,2)` and rounded to 2 decimals (no floating-point errors in the DB). JSON returns numbers.
- **Balance:** `amount − paid_amount`. Late fees and discounts are policy text only and are not computed by the API.
- **Pay Tuition:**
  - Amount must be > 0 and ≤ the term's remaining balance (422 otherwise).
  - A fully paid term returns 409 `ALREADY_PAID`.
  - A `transactionId` is unique system-wide (409 on reuse, enforced by a DB constraint even under concurrency).
  - The tuition row is locked (`SELECT … FOR UPDATE`) during payment.
  - Payments are simulated; no card processing.
- **Add Tuition duplicate rule:** `(studentNo, term)` is unique.
  - An existing record is **updated** (`UPDATED`).
  - The update is rejected with 409 if the new amount is below what has already been paid, or if the currency changes after a payment.
  - Unknown student numbers are created, since admin is treated as the source of truth for enrolment.
- **Batch:** each CSV row runs in its own savepoint, so valid rows are kept and failures are reported with line numbers. Status is `COMPLETED`, `PARTIAL` or `FAILED`.
- **Mobile limit:** 3 calls per student per **UTC calendar day**. Every call counts, including 404s.
- **Agent identity:** the agent acts only as the logged-in user. Admin features are not exposed to the agent.
- **Gateway JWT secret:** `kong.yml` and `docker-compose.yml` contain a development secret. Production must use a different secret held in a secret store.

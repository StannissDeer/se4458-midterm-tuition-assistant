"""Unit tests: every REST operation has success and failure/edge-case tests."""
from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings
from tests.conftest import bearer

V1 = "/api/v1"


# ============================================================ Auth
class TestAuth:
    def test_login_success_returns_jwt(self, client):
        r = client.post(f"{V1}/auth/login", json={"username": "ayse", "password": "pw"})
        assert r.status_code == 200
        body = r.json()
        claims = jwt.decode(body["access_token"], settings.JWT_SECRET, algorithms=["HS256"],
                            issuer=settings.JWT_ISSUER)
        assert claims["student_no"] == "1001" and claims["role"] == "student"

    def test_login_wrong_password(self, client):
        r = client.post(f"{V1}/auth/login", json={"username": "ayse", "password": "nope"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_login_validation_error(self, client):
        r = client.post(f"{V1}/auth/login", json={"username": ""})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ============================================================ Mobile: Query Tuition
class TestMobileQuery:
    def test_overall_balance_without_auth(self, client):
        r = client.get(f"{V1}/mobile/tuition/1001")
        assert r.status_code == 200
        b = r.json()
        assert b["scope"] == "OVERALL"
        assert b["tuitionTotal"] == 90000 and b["paidAmount"] == 15000 and b["balance"] == 75000
        assert len(b["terms"]) == 2
        assert r.headers["X-RateLimit-Remaining"] == "2"

    def test_term_balance(self, client):
        r = client.get(f"{V1}/mobile/tuition/1001", params={"term": "2026-FALL"})
        assert r.status_code == 200
        assert r.json()["scope"] == "TERM" and r.json()["balance"] == 30000

    def test_limit_three_per_student_per_day(self, client):
        for expected in ("2", "1", "0"):
            r = client.get(f"{V1}/mobile/tuition/1001")
            assert r.status_code == 200 and r.headers["X-RateLimit-Remaining"] == expected
        r = client.get(f"{V1}/mobile/tuition/1001")
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    def test_limit_is_keyed_by_student_not_client(self, client):
        for _ in range(4):
            client.get(f"{V1}/mobile/tuition/1001")
        # same client/IP, different student -> still allowed
        assert client.get(f"{V1}/mobile/tuition/1003").status_code == 200

    def test_unknown_student(self, client):
        r = client.get(f"{V1}/mobile/tuition/9999")
        assert r.status_code == 404 and r.json()["error"]["code"] == "STUDENT_NOT_FOUND"

    def test_invalid_term_format(self, client):
        r = client.get(f"{V1}/mobile/tuition/1001", params={"term": "fall"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_TERM"

    def test_unknown_term(self, client):
        r = client.get(f"{V1}/mobile/tuition/1001", params={"term": "2030-FALL"})
        assert r.status_code == 404 and r.json()["error"]["code"] == "TUITION_NOT_FOUND"


# ============================================================ Banking: Query Tuition
class TestBankingQuery:
    def test_own_record(self, client, student_headers):
        r = client.get(f"{V1}/banking/tuition/1001", headers=student_headers)
        assert r.status_code == 200 and r.json()["balance"] == 75000

    def test_no_daily_limit(self, client, student_headers):
        for _ in range(5):
            assert client.get(f"{V1}/banking/tuition/1001", headers=student_headers).status_code == 200

    def test_requires_token(self, client):
        r = client.get(f"{V1}/banking/tuition/1001")
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"

    def test_cannot_read_other_student(self, client, student_headers):
        r = client.get(f"{V1}/banking/tuition/1002", headers=student_headers)
        assert r.status_code == 403

    def test_admin_can_read_any(self, client, admin_headers):
        assert client.get(f"{V1}/banking/tuition/1002", headers=admin_headers).status_code == 200

    def test_expired_token(self, client):
        token = jwt.encode(
            {"iss": settings.JWT_ISSUER, "sub": "ayse", "role": "student", "student_no": "1001",
             "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
            settings.JWT_SECRET, algorithm="HS256")
        r = client.get(f"{V1}/banking/tuition/1001", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"

    def test_tampered_token(self, client):
        token = jwt.encode({"iss": settings.JWT_ISSUER, "sub": "x", "role": "admin",
                            "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                           "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
        r = client.get(f"{V1}/banking/tuition/1002", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"


# ============================================================ Banking: Pay Tuition
def pay(client, headers, amount, txn="TX-1", term="2026-FALL", student="1001"):
    return client.post(f"{V1}/banking/tuition/payments", headers=headers,
                       json={"studentNo": student, "term": term, "amount": amount, "transactionId": txn})


class TestPayTuition:
    def test_partial_payment(self, client, student_headers):
        r = pay(client, student_headers, 10000)
        assert r.status_code == 201
        b = r.json()
        assert b["paymentStatus"] == "SUCCESS"
        assert b["paidAmount"] == 10000 and b["totalPaid"] == 25000 and b["remainingBalance"] == 20000

    def test_full_payment_then_already_paid(self, client, student_headers):
        assert pay(client, student_headers, 30000, "TX-A").json()["remainingBalance"] == 0
        r = pay(client, student_headers, 1, "TX-B")
        assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_PAID"

    def test_duplicate_transaction_rejected_and_balance_unchanged(self, client, student_headers):
        assert pay(client, student_headers, 1000, "DUP").status_code == 201
        r = pay(client, student_headers, 1000, "DUP")
        assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_TRANSACTION"
        q = client.get(f"{V1}/banking/tuition/1001", params={"term": "2026-FALL"}, headers=student_headers)
        assert q.json()["balance"] == 29000

    def test_amount_exceeding_balance(self, client, student_headers):
        r = pay(client, student_headers, 30000.01)
        assert r.status_code == 422 and r.json()["error"]["code"] == "AMOUNT_EXCEEDS_BALANCE"

    def test_zero_or_negative_amount(self, client, student_headers):
        assert pay(client, student_headers, 0).status_code == 422
        assert pay(client, student_headers, -5).status_code == 422

    def test_no_tuition_for_term(self, client, student_headers):
        r = pay(client, student_headers, 10, term="2030-FALL")
        assert r.status_code == 404

    def test_cannot_pay_for_other_student(self, client, student_headers):
        assert pay(client, student_headers, 10, student="1003").status_code == 403

    def test_requires_token(self, client):
        assert pay(client, {}, 10).status_code == 401


# ============================================================ Admin: Add Tuition
class TestAddTuition:
    def test_create_new(self, client, admin_headers):
        r = client.post(f"{V1}/admin/tuition", headers=admin_headers,
                        json={"studentNo": "1003", "term": "2027-SPRING", "amount": 3000, "currency": "USD"})
        assert r.status_code == 200 and r.json()["transactionStatus"] == "CREATED"

    def test_duplicate_updates_existing(self, client, admin_headers):
        r = client.post(f"{V1}/admin/tuition", headers=admin_headers,
                        json={"studentNo": "1001", "term": "2026-FALL", "amount": 50000, "currency": "TRY"})
        assert r.json()["transactionStatus"] == "UPDATED" and r.json()["balance"] == 35000

    def test_update_below_paid_rejected(self, client, admin_headers):
        r = client.post(f"{V1}/admin/tuition", headers=admin_headers,
                        json={"studentNo": "1001", "term": "2026-FALL", "amount": 100, "currency": "TRY"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "AMOUNT_BELOW_PAID"

    def test_currency_change_after_payment_rejected(self, client, admin_headers):
        r = client.post(f"{V1}/admin/tuition", headers=admin_headers,
                        json={"studentNo": "1001", "term": "2026-FALL", "amount": 50000, "currency": "USD"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "CURRENCY_LOCKED"

    def test_new_student_is_created(self, client, admin_headers):
        r = client.post(f"{V1}/admin/tuition", headers=admin_headers,
                        json={"studentNo": "2000", "term": "2026-FALL", "amount": 1000, "currency": "TRY"})
        assert r.json()["transactionStatus"] == "CREATED"
        assert client.get(f"{V1}/mobile/tuition/2000").json()["balance"] == 1000

    def test_invalid_input(self, client, admin_headers):
        r = client.post(f"{V1}/admin/tuition", headers=admin_headers,
                        json={"studentNo": "1001", "term": "FALL-26", "amount": -1, "currency": "GBP"})
        assert r.status_code == 422

    def test_student_role_forbidden(self, client, student_headers):
        r = client.post(f"{V1}/admin/tuition", headers=student_headers,
                        json={"studentNo": "1001", "term": "2026-FALL", "amount": 1, "currency": "TRY"})
        assert r.status_code == 403


# ============================================================ Admin: Add Tuition Batch
def upload(client, headers, text):
    return client.post(f"{V1}/admin/tuition/batch", headers=headers,
                       files={"file": ("t.csv", text.encode(), "text/csv")})


class TestAddTuitionBatch:
    def test_all_rows_succeed(self, client, admin_headers):
        r = upload(client, admin_headers,
                   "studentNo,term,amount,currency\n1003,2027-SPRING,3000,USD\n1002,2027-SPRING,45000,TRY\n")
        b = r.json()
        assert r.status_code == 200 and b["transactionStatus"] == "COMPLETED"
        assert b["successCount"] == 2 and b["failureCount"] == 0 and b["created"] == 2

    def test_partial_failure_reports_counts(self, client, admin_headers):
        csv = ("studentNo,term,amount,currency\n"
               "1003,2027-SPRING,3000,USD\n"      # created
               "1001,2026-FALL,46000,TRY\n"       # updated
               "1001,2026-FALL,10,TRY\n"          # below paid
               "1002,BADTERM,100,TRY\n"           # invalid term
               "1002,2027-SUMMER,abc,TRY\n")      # invalid amount
        b = upload(client, admin_headers, csv).json()
        assert b["transactionStatus"] == "PARTIAL"
        assert (b["totalRows"], b["successCount"], b["failureCount"]) == (5, 2, 3)
        assert (b["created"], b["updated"]) == (1, 1)
        assert [e["line"] for e in b["errors"]] == [4, 5, 6]

    def test_bad_header(self, client, admin_headers):
        r = upload(client, admin_headers, "id,amount\n1,2\n")
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CSV"

    def test_requires_admin(self, client, student_headers):
        assert upload(client, student_headers, "studentNo,term,amount,currency\n").status_code == 403


# ============================================================ Admin: Unpaid Tuition Status
class TestUnpaidStatus:
    def test_paged_list(self, client, admin_headers):
        r = client.get(f"{V1}/admin/tuition/unpaid", headers=admin_headers,
                       params={"term": "2026-FALL", "page": 1, "pageSize": 1})
        b = r.json()
        assert r.status_code == 200
        assert b["totalItems"] == 2 and b["totalPages"] == 2  # 1002 fully paid -> excluded
        assert b["items"][0]["studentNo"] == "1001" and b["items"][0]["balance"] == 30000

    def test_second_page(self, client, admin_headers):
        b = client.get(f"{V1}/admin/tuition/unpaid", headers=admin_headers,
                       params={"term": "2026-FALL", "page": 2, "pageSize": 1}).json()
        assert b["items"][0]["studentNo"] == "1003" and b["items"][0]["currency"] == "USD"

    def test_page_beyond_end_is_empty(self, client, admin_headers):
        b = client.get(f"{V1}/admin/tuition/unpaid", headers=admin_headers,
                       params={"term": "2026-FALL", "page": 9}).json()
        assert b["items"] == []

    def test_invalid_page_size(self, client, admin_headers):
        r = client.get(f"{V1}/admin/tuition/unpaid", headers=admin_headers,
                       params={"term": "2026-FALL", "pageSize": 1000})
        assert r.status_code == 422

    def test_requires_admin(self, client, student_headers):
        r = client.get(f"{V1}/admin/tuition/unpaid", headers=student_headers, params={"term": "2026-FALL"})
        assert r.status_code == 403

    def test_requires_token(self, client):
        assert client.get(f"{V1}/admin/tuition/unpaid", params={"term": "2026-FALL"}).status_code == 401


# ============================================================ Versioning / docs
def test_openapi_lists_versioned_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert all(p.startswith("/api/v1/") or p == "/health" for p in paths)
    assert "/api/v1/banking/tuition/payments" in paths


# ============================================================ Gateway-only access
class TestGatewaySecret:
    def test_direct_call_without_gateway_secret_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(settings, "GATEWAY_SECRET", "s3cret")
        r = client.get(f"{V1}/mobile/tuition/1001")
        assert r.status_code == 403 and r.json()["error"]["code"] == "GATEWAY_REQUIRED"

    def test_call_with_gateway_secret_is_allowed(self, client, monkeypatch):
        monkeypatch.setattr(settings, "GATEWAY_SECRET", "s3cret")
        r = client.get(f"{V1}/mobile/tuition/1001", headers={"X-Gateway-Secret": "s3cret"})
        assert r.status_code == 200

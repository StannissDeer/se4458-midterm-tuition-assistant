# ER / Data Model

```mermaid
erDiagram
    STUDENTS ||--o{ TUITIONS : "is assessed"
    STUDENTS ||--o| USERS : "logs in as"
    TUITIONS ||--o{ PAYMENTS : "is paid by"
    STUDENTS ||--o{ MOBILE_QUERY_USAGE : "daily mobile quota"

    STUDENTS {
        varchar student_no PK
        varchar full_name
        varchar email
    }
    USERS {
        int id PK
        varchar username UK
        varchar password_hash "PBKDF2-SHA256"
        varchar role "student | admin"
        varchar student_no FK "null for admin"
    }
    TUITIONS {
        int id PK
        varchar student_no FK
        varchar term "e.g. 2026-FALL"
        numeric amount "> 0"
        varchar currency "TRY | USD | EUR"
        numeric paid_amount "0 <= paid <= amount"
        timestamptz created_at
        timestamptz updated_at
    }
    PAYMENTS {
        int id PK
        varchar transaction_id UK "duplicate guard"
        int tuition_id FK
        varchar student_no
        varchar term
        numeric amount
        varchar status
        timestamptz created_at
    }
    MOBILE_QUERY_USAGE {
        varchar student_no PK
        date query_date PK
        int count
    }
```

Constraints:
- `UNIQUE(student_no, term)` on `tuitions`: one assessment per student per term (Add Tuition upserts).
- `UNIQUE(transaction_id)` on `payments`: rejects duplicate payments, even under concurrent requests.
- `CHECK(paid_amount <= amount)`: the database itself prevents overpayment.
- Balance = `amount - paid_amount` (derived, not stored).
- `mobile_query_usage` counts mobile queries per **student and day** (atomic `INSERT … ON CONFLICT DO UPDATE`).

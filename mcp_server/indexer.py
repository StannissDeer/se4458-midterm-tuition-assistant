"""Splits the policy Markdown into section chunks and builds a BM25 full-text index.

The index is built ONCE (python indexer.py → index.json, or on server start-up if the file is
missing/stale) and is only read when answering queries - documents are never re-indexed per question.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_DOC = HERE / "docs" / "University_Tuition_Policies.md"
DEFAULT_INDEX = HERE / "index.json"

STOPWORDS = set(
    """a an the and or of to in on for at by with from is are was were be been being it its this that
    these those as if then than but not no can could may might will would shall should do does did
    i my me we our you your he she they them what when where which who whom why how there here
    any all each every some such only also into out up down over under about after before""".split()
)
# Small synonym map so student phrasing reaches policy vocabulary.
SYNONYMS = {
    "deadline": ["due"], "due": ["deadline"], "installment": ["plan", "eligibility"],
    "instalment": ["installment", "plan"], "pay": ["payment"], "fee": ["charge"], "late": ["overdue"],
    "refund": ["withdraw"], "withdraw": ["refund"], "bank": ["banking", "iban", "transfer"],
    "iban": ["bank", "transfer"], "scholarship": ["discount"], "discount": ["scholarship"],
    "balance": ["outstanding"], "outstanding": ["balance"], "partial": ["part"],
}


def stem(tok: str) -> str:
    for suf in ("ments", "ment", "ings", "ing", "ies", "ied", "es", "ed", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return tok


def tokenize(text: str, expand: bool = False) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]
    out = []
    for t in toks:
        out.append(stem(t))
        if expand:
            out.extend(stem(s) for s in SYNONYMS.get(t, []))
    return out


@dataclass
class Chunk:
    id: str           # section ID, e.g. "3.2"
    heading: str      # e.g. "Number and size of installments"
    parent: str       # e.g. "3. Installment policy"
    text: str

    @property
    def section(self) -> str:
        return f"§{self.id} {self.heading}"


def split_markdown(md: str) -> list[Chunk]:
    """One chunk per '###' subsection; a '##' section without subsections becomes one chunk."""
    chunks: list[Chunk] = []
    parent, cur_head, buf = "", None, []

    def flush():
        if cur_head and "\n".join(buf).strip():
            m = re.match(r"(\d+(?:\.\d+)*)\.?\s+(.*)", cur_head)
            cid, title = (m.group(1), m.group(2)) if m else (cur_head, cur_head)
            chunks.append(Chunk(cid, title, parent, "\n".join(buf).strip()))

    for line in md.splitlines():
        if line.startswith("## "):
            flush()
            parent, cur_head, buf = line[3:].strip(), line[3:].strip(), []
        elif line.startswith("### "):
            flush()
            cur_head, buf = line[4:].strip(), []
        elif line.startswith("# ") or line.startswith(">"):
            continue
        else:
            buf.append(line)
    flush()
    return chunks


def document_id(md: str) -> str:
    m = re.search(r"\*\*Document ID:\*\*\s*([A-Za-z0-9._-]+)", md)
    return m.group(1) if m else ""


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75, source_hash: str = "",
                 doc_id: str = ""):
        self.chunks, self.k1, self.b, self.source_hash, self.doc_id = chunks, k1, b, source_hash, doc_id
        # title words are indexed twice so headings weigh more
        self.docs = [tokenize(f"{c.section} {c.section} {c.parent} {c.text}") for c in chunks]
        self.tf = [Counter(d) for d in self.docs]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        q = tokenize(query, expand=True)
        scored = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = 0.0
            for t in set(q):
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [
            {
                "section": self.chunks[i].id,
                "heading": self.chunks[i].heading,
                "parentSection": self.chunks[i].parent,
                "score": round(s, 3),
                "text": self.chunks[i].text,
            }
            for s, i in scored[:top_k]
        ]

    # ---- persistence ----
    def save(self, path: Path) -> None:
        data = {"source_hash": self.source_hash, "doc_id": self.doc_id, "k1": self.k1, "b": self.b,
                "chunks": [asdict(c) for c in self.chunks]}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls([Chunk(**c) for c in data["chunks"]], data["k1"], data["b"], data["source_hash"],
                   data.get("doc_id", ""))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_index(doc: Path = DEFAULT_DOC, out: Path = DEFAULT_INDEX) -> BM25Index:
    md = doc.read_text(encoding="utf-8")
    idx = BM25Index(split_markdown(md), source_hash=file_hash(doc), doc_id=document_id(md))
    idx.save(out)
    return idx


def load_or_build(doc: Path = DEFAULT_DOC, out: Path = DEFAULT_INDEX) -> BM25Index:
    if out.exists():
        idx = BM25Index.load(out)
        if idx.source_hash == file_hash(doc):
            return idx
    return build_index(doc, out)


if __name__ == "__main__":
    idx = build_index()
    print(f"Indexed {len(idx.chunks)} chunks from {DEFAULT_DOC.name} -> {DEFAULT_INDEX.name}")

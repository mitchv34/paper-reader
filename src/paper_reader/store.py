"""Persistent RAG store: SQLite + numpy embeddings for ingested papers."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

import numpy as np

from paper_reader.converter import convert_to_chunks

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    zotero_item_key TEXT UNIQUE,
    file_path       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    title           TEXT,
    authors         TEXT,
    section_outline TEXT,
    total_chunks    INTEGER NOT NULL,
    total_tokens    INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    ingested_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id       INTEGER NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    chunk_index    INTEGER NOT NULL,
    section_path   TEXT NOT NULL,
    section_title  TEXT NOT NULL,
    section_level  INTEGER NOT NULL,
    content        TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    embedding      BLOB,
    UNIQUE(paper_id, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, section_path,
    content='chunks', content_rowid='chunk_id'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content, section_path)
    VALUES (new.chunk_id, new.content, new.section_path);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content, section_path)
    VALUES('delete', old.chunk_id, old.content, old.section_path);
END;

CREATE INDEX IF NOT EXISTS idx_papers_zotero_key ON papers(zotero_item_key);
CREATE INDEX IF NOT EXISTS idx_chunks_paper_id ON chunks(paper_id);
"""


def _default_db_path() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(xdg) / "paper-reader" / "papers.db"


class PaperStore:
    """Persistent store for ingested paper chunks with semantic search."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path(
            os.environ.get("PAPER_READER_DB", "") or _default_db_path()
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        self._model = None
        self._emb_matrix: Optional[np.ndarray] = None
        self._emb_chunk_ids: Optional[list[int]] = None
        self._emb_dirty = True

    # ------------------------------------------------------------------
    # Embedding model (lazy)
    # ------------------------------------------------------------------

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(EMBEDDING_MODEL)
        return self._model

    def _embed(self, texts: list[str]) -> np.ndarray:
        model = self._get_model()
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return embeddings.astype(np.float32)

    # ------------------------------------------------------------------
    # Embedding matrix cache
    # ------------------------------------------------------------------

    def _load_embedding_matrix(self):
        cur = self._conn.execute("SELECT chunk_id, embedding FROM chunks WHERE embedding IS NOT NULL")
        rows = cur.fetchall()
        if not rows:
            self._emb_matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            self._emb_chunk_ids = []
        else:
            ids, blobs = zip(*rows)
            self._emb_chunk_ids = list(ids)
            self._emb_matrix = np.stack(
                [np.frombuffer(b, dtype=np.float32) for b in blobs]
            )
        self._emb_dirty = False

    # ------------------------------------------------------------------
    # Backend selection
    # ------------------------------------------------------------------

    def _convert(
        self,
        file_path: str,
        arxiv_id: str = "",
        doi: str = "",
        url: str = "",
        max_tokens: int = 2000,
        page_range: str | None = None,
        backend: str = "auto",
    ) -> tuple[dict, str]:
        """Convert PDF to chunks using the selected backend.

        Returns (chunks_result, backend_name).
        """
        if backend == "arxiv" or backend == "auto":
            # Try arXiv source
            from paper_reader.arxiv_source import (
                extract_arxiv_id, fetch_arxiv_source, parse_latex_to_chunks,
            )
            aid = extract_arxiv_id(doi=doi, url=url, arxiv_id=arxiv_id)
            if aid:
                source = fetch_arxiv_source(aid)
                if source:
                    result = parse_latex_to_chunks(source["tex_content"], max_tokens=max_tokens)
                    result["metadata"]["source_path"] = file_path
                    return result, "arxiv"

            if backend == "arxiv":
                raise ValueError(f"arXiv source not found for: arxiv_id={arxiv_id}, doi={doi}, url={url}")

        if backend == "fast" or backend == "auto":
            from paper_reader.fast_converter import convert_to_chunks_fast
            result = convert_to_chunks_fast(file_path, max_tokens=max_tokens, page_range=page_range)
            return result, "fast"

        if backend == "marker":
            result = convert_to_chunks(file_path, max_tokens=max_tokens, page_range=page_range)
            return result, "marker"

        raise ValueError(f"Unknown backend: {backend!r}. Use 'auto', 'arxiv', 'fast', or 'marker'.")

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(
        self,
        file_path: str,
        zotero_item_key: str = "",
        title: str = "",
        authors: str = "",
        arxiv_id: str = "",
        doi: str = "",
        url: str = "",
        max_tokens: int = 2000,
        page_range: str | None = None,
        backend: str = "auto",
        force: bool = False,
    ) -> dict:
        """Convert a PDF, compute embeddings, and store in the database.

        Backend options:
            "auto"   - Try arXiv source first, fall back to fast hybrid.
            "arxiv"  - arXiv LaTeX source only (fails if not available).
            "fast"   - PyMuPDF4LLM (fast, no GPU).
            "marker" - marker-pdf (slow, highest quality).
        """
        t0 = time.time()

        zk = zotero_item_key or None

        # Check for existing paper
        existing = None
        if zk:
            existing = self._conn.execute(
                "SELECT paper_id, content_hash FROM papers WHERE zotero_item_key = ?", (zk,)
            ).fetchone()
        if not existing:
            existing = self._conn.execute(
                "SELECT paper_id, content_hash FROM papers WHERE file_path = ?", (file_path,)
            ).fetchone()

        # Convert to chunks using selected backend
        result, used_backend = self._convert(
            file_path, arxiv_id=arxiv_id, doi=doi, url=url,
            max_tokens=max_tokens, page_range=page_range, backend=backend,
        )
        new_hash = result["metadata"]["content_hash"]

        # Skip if unchanged
        if existing and not force:
            if existing[1] == new_hash:
                return {
                    "status": "skipped",
                    "reason": "content_hash unchanged",
                    "paper_id": existing[0],
                    "content_hash": new_hash,
                }

        # Remove old data if re-ingesting
        if existing:
            self._conn.execute("DELETE FROM papers WHERE paper_id = ?", (existing[0],))

        # Compute embeddings
        texts = [c["content"] for c in result["chunks"]]
        embeddings = self._embed(texts) if texts else np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

        # Auto-extract title from first section if not provided
        if not title and result["sections"]:
            title = result["sections"][0].get("title", "")

        # Insert paper
        cur = self._conn.execute(
            """INSERT INTO papers (zotero_item_key, file_path, content_hash, title, authors,
               section_outline, total_chunks, total_tokens, embedding_model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                zk, file_path, new_hash, title, authors,
                json.dumps(result["sections"]),
                result["metadata"]["total_chunks"],
                result["metadata"]["total_tokens"],
                EMBEDDING_MODEL,
            ),
        )
        paper_id = cur.lastrowid

        # Insert chunks
        for i, chunk in enumerate(result["chunks"]):
            emb_blob = embeddings[i].tobytes() if i < len(embeddings) else None
            self._conn.execute(
                """INSERT INTO chunks (paper_id, chunk_index, section_path, section_title,
                   section_level, content, token_estimate, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    paper_id, i, chunk["section_path"], chunk["section_title"],
                    chunk["section_level"], chunk["content"],
                    chunk["token_estimate"], emb_blob,
                ),
            )

        self._conn.commit()
        self._emb_dirty = True

        elapsed = time.time() - t0
        return {
            "status": "ingested",
            "backend": used_backend,
            "paper_id": paper_id,
            "title": title,
            "total_chunks": len(result["chunks"]),
            "total_tokens": result["metadata"]["total_tokens"],
            "content_hash": new_hash,
            "elapsed_seconds": round(elapsed, 1),
        }

    # ------------------------------------------------------------------
    # Semantic search
    # ------------------------------------------------------------------

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        paper_filter: str = "",
        section_filter: str = "",
    ) -> list[dict]:
        """Search across all ingested papers by semantic similarity."""
        if self._emb_dirty:
            self._load_embedding_matrix()

        if len(self._emb_chunk_ids) == 0:
            return []

        query_emb = self._embed([query])[0]
        scores = self._emb_matrix @ query_emb

        # Get metadata for all chunks
        chunk_meta = {}
        rows = self._conn.execute(
            """SELECT c.chunk_id, c.paper_id, c.chunk_index, c.section_path,
                      c.section_title, c.content, c.token_estimate,
                      p.title, p.authors, p.zotero_item_key
               FROM chunks c JOIN papers p ON c.paper_id = p.paper_id"""
        ).fetchall()
        for r in rows:
            chunk_meta[r[0]] = {
                "chunk_id": r[0], "paper_id": r[1], "chunk_index": r[2],
                "section_path": r[3], "section_title": r[4],
                "content": r[5], "token_estimate": r[6],
                "paper_title": r[7], "authors": r[8], "zotero_item_key": r[9],
            }

        # Rank and filter
        ranked = sorted(
            zip(self._emb_chunk_ids, scores), key=lambda x: x[1], reverse=True
        )

        results = []
        for cid, score in ranked:
            if cid not in chunk_meta:
                continue
            meta = chunk_meta[cid]

            if paper_filter:
                pf = paper_filter.lower()
                if not (
                    (meta.get("paper_title") or "").lower().find(pf) >= 0
                    or (meta.get("zotero_item_key") or "") == paper_filter
                ):
                    continue

            if section_filter:
                if section_filter.lower() not in meta["section_path"].lower():
                    continue

            meta["score"] = round(float(score), 4)
            results.append(meta)
            if len(results) >= top_k:
                break

        return results

    # ------------------------------------------------------------------
    # Keyword search (FTS5)
    # ------------------------------------------------------------------

    def keyword_search(
        self, query: str, top_k: int = 10, paper_filter: str = ""
    ) -> list[dict]:
        """Full-text keyword search using SQLite FTS5."""
        sql = """
            SELECT c.chunk_id, c.paper_id, c.chunk_index, c.section_path,
                   c.section_title, c.content, c.token_estimate,
                   p.title, p.authors, p.zotero_item_key,
                   rank
            FROM chunks_fts
            JOIN chunks c ON chunks_fts.rowid = c.chunk_id
            JOIN papers p ON c.paper_id = p.paper_id
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        rows = self._conn.execute(sql, (query, top_k * 3)).fetchall()

        results = []
        for r in rows:
            meta = {
                "chunk_id": r[0], "paper_id": r[1], "chunk_index": r[2],
                "section_path": r[3], "section_title": r[4],
                "content": r[5], "token_estimate": r[6],
                "paper_title": r[7], "authors": r[8], "zotero_item_key": r[9],
                "bm25_score": round(float(r[10]), 4),
            }
            if paper_filter:
                pf = paper_filter.lower()
                if not (
                    (meta.get("paper_title") or "").lower().find(pf) >= 0
                    or (meta.get("zotero_item_key") or "") == paper_filter
                ):
                    continue
            results.append(meta)
            if len(results) >= top_k:
                break

        return results

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_chunks(
        self,
        paper_id: str,
        section_filter: str = "",
        offset: int = 0,
        limit: int = 10,
    ) -> dict:
        """Retrieve chunks from a specific paper."""
        pid = self._resolve_paper_id(paper_id)
        if pid is None:
            return {"error": f"Paper not found: {paper_id}"}

        paper = self._conn.execute(
            "SELECT paper_id, title, authors, zotero_item_key, section_outline, total_chunks, total_tokens FROM papers WHERE paper_id = ?",
            (pid,),
        ).fetchone()

        sql = """SELECT chunk_id, chunk_index, section_path, section_title,
                        section_level, content, token_estimate
                 FROM chunks WHERE paper_id = ?"""
        params: list = [pid]

        if section_filter:
            sql += " AND section_path LIKE ?"
            params.append(f"%{section_filter}%")

        sql += " ORDER BY chunk_index LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(sql, params).fetchall()
        chunks = [
            {
                "chunk_id": r[0], "chunk_index": r[1], "section_path": r[2],
                "section_title": r[3], "section_level": r[4],
                "content": r[5], "token_estimate": r[6],
            }
            for r in rows
        ]

        return {
            "paper_id": paper[0],
            "title": paper[1],
            "authors": paper[2],
            "zotero_item_key": paper[3],
            "section_outline": json.loads(paper[4]) if paper[4] else [],
            "total_chunks": paper[5],
            "total_tokens": paper[6],
            "chunks": chunks,
        }

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def list_papers(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT paper_id, title, authors, zotero_item_key, file_path,
                      total_chunks, total_tokens, content_hash, ingested_at
               FROM papers ORDER BY ingested_at DESC"""
        ).fetchall()
        return [
            {
                "paper_id": r[0], "title": r[1], "authors": r[2],
                "zotero_item_key": r[3], "file_path": r[4],
                "total_chunks": r[5], "total_tokens": r[6],
                "content_hash": r[7], "ingested_at": r[8],
            }
            for r in rows
        ]

    def remove_paper(self, paper_id: str) -> dict:
        pid = self._resolve_paper_id(paper_id)
        if pid is None:
            return {"error": f"Paper not found: {paper_id}"}

        title = self._conn.execute(
            "SELECT title FROM papers WHERE paper_id = ?", (pid,)
        ).fetchone()[0]

        self._conn.execute("DELETE FROM papers WHERE paper_id = ?", (pid,))
        self._conn.commit()
        self._emb_dirty = True
        return {"status": "removed", "paper_id": pid, "title": title}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_paper_id(self, paper_id_or_key: str) -> Optional[int]:
        try:
            pid = int(paper_id_or_key)
            row = self._conn.execute(
                "SELECT paper_id FROM papers WHERE paper_id = ?", (pid,)
            ).fetchone()
            return row[0] if row else None
        except ValueError:
            row = self._conn.execute(
                "SELECT paper_id FROM papers WHERE zotero_item_key = ?",
                (paper_id_or_key,),
            ).fetchone()
            return row[0] if row else None

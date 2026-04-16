from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import socket
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "share" / "rhea"
CACHE_DIR = Path.home() / ".cache" / "rhea"
DB_PATH = STATE_DIR / "graph_memory.sqlite3"
SOCKET_PATH = CACHE_DIR / "graph_memory.sock"

_WORD_RE = re.compile(r"[a-z0-9']+")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+|\n+")


@dataclass
class QueryResult:
    words: list[dict]
    phrases: list[dict]
    sentences: list[dict]


class GraphMemoryDB:
    def __init__(self, db_path: Path):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS words (
                term TEXT PRIMARY KEY,
                weight INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS phrases (
                text TEXT PRIMARY KEY,
                weight INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sentences (
                text TEXT PRIMARY KEY,
                weight INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS word_phrase (
                word TEXT NOT NULL,
                phrase TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (word, phrase)
            );

            CREATE TABLE IF NOT EXISTS phrase_sentence (
                phrase TEXT NOT NULL,
                sentence TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (phrase, sentence)
            );

            CREATE TABLE IF NOT EXISTS word_adj (
                source_word TEXT NOT NULL,
                target_word TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_word, target_word)
            );

            CREATE TABLE IF NOT EXISTS phrase_adj (
                source_phrase TEXT NOT NULL,
                target_phrase TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_phrase, target_phrase)
            );

            CREATE TABLE IF NOT EXISTS sentence_adj (
                source_sentence TEXT NOT NULL,
                target_sentence TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_sentence, target_sentence)
            );

            CREATE TABLE IF NOT EXISTS kv_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_words_weight ON words(weight DESC);
            CREATE INDEX IF NOT EXISTS idx_phrases_weight ON phrases(weight DESC);
            CREATE INDEX IF NOT EXISTS idx_sentences_weight ON sentences(weight DESC);
            CREATE INDEX IF NOT EXISTS idx_wp_word ON word_phrase(word, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_ps_phrase ON phrase_sentence(phrase, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_word_adj_source ON word_adj(source_word, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_word_adj_target ON word_adj(target_word, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_phrase_adj_source ON phrase_adj(source_phrase, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_phrase_adj_target ON phrase_adj(target_phrase, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_sentence_adj_source ON sentence_adj(source_sentence, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_sentence_adj_target ON sentence_adj(target_sentence, weight DESC);
            """
        )
        self._conn.commit()

    def remember(self, key: str, value: str) -> None:
        ts = int(time.time())
        self._conn.execute(
            """
            INSERT INTO kv_memory(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              updated_at = excluded.updated_at
            """,
            (key, value, ts),
        )
        self._conn.commit()

    def clear(self, scope: str) -> dict[str, int]:
        scope_norm = scope.strip().lower()
        if scope_norm not in {"all", "graph", "kv"}:
            raise ValueError("scope must be one of: all, graph, kv")

        graph_tables = [
            "words",
            "phrases",
            "sentences",
            "word_phrase",
            "phrase_sentence",
            "word_adj",
            "phrase_adj",
            "sentence_adj",
        ]
        cleared: dict[str, int] = {"graph_rows": 0, "kv_rows": 0}

        if scope_norm in {"all", "graph"}:
            for table in graph_tables:
                count_row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                cleared["graph_rows"] += int(count_row[0]) if count_row else 0
                self._conn.execute(f"DELETE FROM {table}")

        if scope_norm in {"all", "kv"}:
            count_row = self._conn.execute("SELECT COUNT(*) FROM kv_memory").fetchone()
            cleared["kv_rows"] = int(count_row[0]) if count_row else 0
            self._conn.execute("DELETE FROM kv_memory")

        self._conn.commit()
        return cleared

    def recall(self, key: str) -> str | None:
        if key == "*":
            rows = self._conn.execute(
                "SELECT key, value FROM kv_memory ORDER BY updated_at DESC"
            ).fetchall()
            if not rows:
                return "Memory is empty."
            return "\n".join(f"{k}: {v}" for k, v in rows)

        row = self._conn.execute(
            "SELECT value FROM kv_memory WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return str(row[0])

    def ingest(self, text: str) -> dict:
        normalized = _normalize_text(text)
        if not normalized:
            return {"ingested": False, "reason": "empty"}

        sentence_list = _split_sentences(normalized)
        if not sentence_list:
            return {"ingested": False, "reason": "no_sentences"}

        word_counts: Counter[str] = Counter()
        phrase_counts: Counter[str] = Counter()
        sentence_counts: Counter[str] = Counter(sentence_list)
        word_phrase_counts: Counter[tuple[str, str]] = Counter()
        phrase_sentence_counts: Counter[tuple[str, str]] = Counter()
        word_adj_counts: Counter[tuple[str, str]] = Counter()
        phrase_adj_counts: Counter[tuple[str, str]] = Counter()
        sentence_adj_counts: Counter[tuple[str, str]] = Counter()

        for i in range(0, len(sentence_list) - 1):
            sentence_adj_counts[(sentence_list[i], sentence_list[i + 1])] += 1

        for sentence in sentence_list:
            words = _split_words(sentence)
            if not words:
                continue

            for i in range(0, len(words) - 1):
                word_adj_counts[(words[i], words[i + 1])] += 1

            local_word_counts = Counter(words)
            word_counts.update(local_word_counts)

            phrases_by_n = _extract_phrases_by_n(words)
            phrases = [
                phrase
                for n in (2, 3, 4)
                for phrase in phrases_by_n.get(n, [])
            ]
            local_phrase_counts = Counter(phrases)
            phrase_counts.update(local_phrase_counts)

            for n in (2, 3, 4):
                sequence = phrases_by_n.get(n, [])
                for i in range(0, len(sequence) - 1):
                    phrase_adj_counts[(sequence[i], sequence[i + 1])] += 1

            for phrase, count in local_phrase_counts.items():
                phrase_sentence_counts[(phrase, sentence)] += count
                for word in set(phrase.split(" ")):
                    word_phrase_counts[(word, phrase)] += count

        cur = self._conn.cursor()

        cur.executemany(
            """
            INSERT INTO words(term, weight)
            VALUES(?, ?)
            ON CONFLICT(term) DO UPDATE SET weight = words.weight + excluded.weight
            """,
            [(term, weight) for term, weight in word_counts.items()],
        )

        cur.executemany(
            """
            INSERT INTO phrases(text, weight)
            VALUES(?, ?)
            ON CONFLICT(text) DO UPDATE SET weight = phrases.weight + excluded.weight
            """,
            [(phrase, weight) for phrase, weight in phrase_counts.items()],
        )

        cur.executemany(
            """
            INSERT INTO sentences(text, weight)
            VALUES(?, ?)
            ON CONFLICT(text) DO UPDATE SET weight = sentences.weight + excluded.weight
            """,
            [(sentence, weight) for sentence, weight in sentence_counts.items()],
        )

        cur.executemany(
            """
            INSERT INTO word_phrase(word, phrase, weight)
            VALUES(?, ?, ?)
            ON CONFLICT(word, phrase) DO UPDATE SET weight = word_phrase.weight + excluded.weight
            """,
            [(word, phrase, weight) for (word, phrase), weight in word_phrase_counts.items()],
        )

        cur.executemany(
            """
            INSERT INTO phrase_sentence(phrase, sentence, weight)
            VALUES(?, ?, ?)
            ON CONFLICT(phrase, sentence) DO UPDATE SET weight = phrase_sentence.weight + excluded.weight
            """,
            [
                (phrase, sentence, weight)
                for (phrase, sentence), weight in phrase_sentence_counts.items()
            ],
        )

        cur.executemany(
            """
            INSERT INTO word_adj(source_word, target_word, weight)
            VALUES(?, ?, ?)
            ON CONFLICT(source_word, target_word) DO UPDATE SET weight = word_adj.weight + excluded.weight
            """,
            [
                (source_word, target_word, weight)
                for (source_word, target_word), weight in word_adj_counts.items()
            ],
        )

        cur.executemany(
            """
            INSERT INTO phrase_adj(source_phrase, target_phrase, weight)
            VALUES(?, ?, ?)
            ON CONFLICT(source_phrase, target_phrase) DO UPDATE SET weight = phrase_adj.weight + excluded.weight
            """,
            [
                (source_phrase, target_phrase, weight)
                for (source_phrase, target_phrase), weight in phrase_adj_counts.items()
            ],
        )

        cur.executemany(
            """
            INSERT INTO sentence_adj(source_sentence, target_sentence, weight)
            VALUES(?, ?, ?)
            ON CONFLICT(source_sentence, target_sentence) DO UPDATE SET weight = sentence_adj.weight + excluded.weight
            """,
            [
                (source_sentence, target_sentence, weight)
                for (source_sentence, target_sentence), weight in sentence_adj_counts.items()
            ],
        )

        self._conn.commit()
        return {
            "ingested": True,
            "sentences": len(sentence_counts),
            "phrases": len(phrase_counts),
            "words": len(word_counts),
        }

    def query(self, query: str, top_k: int = 5) -> QueryResult:
        query_norm = _normalize_text(query)
        q_words = _split_words(query_norm)

        word_rows = self._conn.execute(
            "SELECT term, weight FROM words ORDER BY weight DESC LIMIT 1500"
        ).fetchall()

        ranked_words: list[dict] = []
        for term, weight in word_rows:
            score = _word_match_score(term, int(weight), q_words)
            ranked_words.append(
                {
                    "word": term,
                    "weight": int(weight),
                    "score": round(score, 4),
                }
            )

        ranked_words.sort(key=lambda x: (x["score"], x["weight"]), reverse=True)

        if q_words:
            top_words = [w for w in ranked_words if w["score"] > 0.45][:top_k]
        else:
            top_words = ranked_words[:top_k]

        if len(top_words) < top_k:
            top_words = ranked_words[:top_k]

        top_words = self._rank_words_with_horizontal(top_words, q_words, top_k)

        phrases = self._rank_phrases(top_words, q_words, top_k)
        sentences = self._rank_sentences(phrases, q_words, top_k)

        return QueryResult(words=top_words, phrases=phrases, sentences=sentences)

    def _rank_words_with_horizontal(self, seed_words: list[dict], q_words: list[str], top_k: int) -> list[dict]:
        if not seed_words:
            return []

        word_scores: dict[str, float] = {}
        word_weights: dict[str, int] = {}

        for item in seed_words:
            word = str(item["word"])
            score = float(item["score"])
            weight = int(item["weight"])
            word_scores[word] = max(word_scores.get(word, 0.0), score + 0.75)
            word_weights[word] = weight

        seeds = [str(item["word"]) for item in seed_words[: max(top_k, 3)]]

        for seed in seeds:
            forward_rows = self._conn.execute(
                """
                SELECT wa.target_word, wa.weight, w.weight
                FROM word_adj wa
                JOIN words w ON w.term = wa.target_word
                WHERE wa.source_word = ?
                ORDER BY wa.weight DESC
                LIMIT 40
                """,
                (seed,),
            ).fetchall()

            backward_rows = self._conn.execute(
                """
                SELECT wa.source_word, wa.weight, w.weight
                FROM word_adj wa
                JOIN words w ON w.term = wa.source_word
                WHERE wa.target_word = ?
                ORDER BY wa.weight DESC
                LIMIT 40
                """,
                (seed,),
            ).fetchall()

            for candidate, edge_weight, node_weight in list(forward_rows) + list(backward_rows):
                candidate_str = str(candidate)
                candidate_score = (
                    float(edge_weight) * 0.9
                    + math.log1p(float(node_weight)) * 0.2
                    + _word_match_score(candidate_str, int(node_weight), q_words) * 0.35
                )
                word_scores[candidate_str] = word_scores.get(candidate_str, 0.0) + candidate_score
                word_weights[candidate_str] = int(node_weight)

        ranked = sorted(
            word_scores.items(),
            key=lambda item: (item[1], word_weights.get(item[0], 0)),
            reverse=True,
        )

        return [
            {
                "word": word,
                "weight": word_weights.get(word, 0),
                "score": round(score, 4),
            }
            for word, score in ranked[:top_k]
        ]

    def _rank_phrases(self, top_words: list[dict], q_words: list[str], top_k: int) -> list[dict]:
        phrase_scores: dict[str, float] = defaultdict(float)
        phrase_weights: dict[str, int] = {}

        for word_item in top_words:
            word = word_item["word"]
            rows = self._conn.execute(
                """
                SELECT wp.phrase, wp.weight, p.weight
                FROM word_phrase wp
                JOIN phrases p ON p.text = wp.phrase
                WHERE wp.word = ?
                ORDER BY wp.weight DESC
                LIMIT 80
                """,
                (word,),
            ).fetchall()
            for phrase, edge_weight, phrase_weight in rows:
                score = (float(edge_weight) * 1.4) + (float(phrase_weight) * 0.25)
                if q_words:
                    score += _phrase_match_score(str(phrase), q_words)
                phrase_scores[str(phrase)] += score
                phrase_weights[str(phrase)] = int(phrase_weight)

        self._blend_phrase_horizontal(phrase_scores, phrase_weights, q_words, top_k)

        if not phrase_scores:
            rows = self._conn.execute(
                "SELECT text, weight FROM phrases ORDER BY weight DESC LIMIT ?",
                (top_k,),
            ).fetchall()
            return [
                {
                    "phrase": str(phrase),
                    "weight": int(weight),
                    "score": round(math.log1p(int(weight)), 4),
                }
                for phrase, weight in rows
            ]

        ranked = sorted(
            phrase_scores.items(),
            key=lambda item: (item[1], phrase_weights.get(item[0], 0)),
            reverse=True,
        )

        out = []
        for phrase, score in ranked[:top_k]:
            out.append(
                {
                    "phrase": phrase,
                    "weight": phrase_weights.get(phrase, 0),
                    "score": round(score, 4),
                }
            )
        return out

    def _blend_phrase_horizontal(
        self,
        phrase_scores: dict[str, float],
        phrase_weights: dict[str, int],
        q_words: list[str],
        top_k: int,
    ) -> None:
        if not phrase_scores:
            return

        seeds = sorted(phrase_scores.items(), key=lambda item: item[1], reverse=True)[: max(top_k, 3)]

        for seed_phrase, _seed_score in seeds:
            forward_rows = self._conn.execute(
                """
                SELECT pa.target_phrase, pa.weight, p.weight
                FROM phrase_adj pa
                JOIN phrases p ON p.text = pa.target_phrase
                WHERE pa.source_phrase = ?
                ORDER BY pa.weight DESC
                LIMIT 40
                """,
                (seed_phrase,),
            ).fetchall()

            backward_rows = self._conn.execute(
                """
                SELECT pa.source_phrase, pa.weight, p.weight
                FROM phrase_adj pa
                JOIN phrases p ON p.text = pa.source_phrase
                WHERE pa.target_phrase = ?
                ORDER BY pa.weight DESC
                LIMIT 40
                """,
                (seed_phrase,),
            ).fetchall()

            for phrase, edge_weight, phrase_weight in list(forward_rows) + list(backward_rows):
                phrase_str = str(phrase)
                score = (float(edge_weight) * 1.0) + (float(phrase_weight) * 0.2)
                if q_words:
                    score += _phrase_match_score(phrase_str, q_words) * 0.5
                phrase_scores[phrase_str] += score
                phrase_weights[phrase_str] = int(phrase_weight)

    def _rank_sentences(self, top_phrases: list[dict], q_words: list[str], top_k: int) -> list[dict]:
        sentence_scores: dict[str, float] = defaultdict(float)
        sentence_weights: dict[str, int] = {}

        for phrase_item in top_phrases:
            phrase = phrase_item["phrase"]
            rows = self._conn.execute(
                """
                SELECT ps.sentence, ps.weight, s.weight
                FROM phrase_sentence ps
                JOIN sentences s ON s.text = ps.sentence
                WHERE ps.phrase = ?
                ORDER BY ps.weight DESC
                LIMIT 100
                """,
                (phrase,),
            ).fetchall()

            for sentence, edge_weight, sentence_weight in rows:
                score = (float(edge_weight) * 1.2) + (float(sentence_weight) * 0.2)
                if q_words:
                    score += _phrase_match_score(str(sentence), q_words)
                sentence_scores[str(sentence)] += score
                sentence_weights[str(sentence)] = int(sentence_weight)

        self._blend_sentence_horizontal(sentence_scores, sentence_weights, q_words, top_k)

        if not sentence_scores:
            rows = self._conn.execute(
                "SELECT text, weight FROM sentences ORDER BY weight DESC LIMIT ?",
                (top_k,),
            ).fetchall()
            return [
                {
                    "sentence": str(sentence),
                    "weight": int(weight),
                    "score": round(math.log1p(int(weight)), 4),
                }
                for sentence, weight in rows
            ]

        ranked = sorted(
            sentence_scores.items(),
            key=lambda item: (item[1], sentence_weights.get(item[0], 0)),
            reverse=True,
        )

        out = []
        for sentence, score in ranked[:top_k]:
            out.append(
                {
                    "sentence": sentence,
                    "weight": sentence_weights.get(sentence, 0),
                    "score": round(score, 4),
                }
            )
        return out

    def _blend_sentence_horizontal(
        self,
        sentence_scores: dict[str, float],
        sentence_weights: dict[str, int],
        q_words: list[str],
        top_k: int,
    ) -> None:
        if not sentence_scores:
            return

        seeds = sorted(sentence_scores.items(), key=lambda item: item[1], reverse=True)[: max(top_k, 3)]

        for seed_sentence, _seed_score in seeds:
            forward_rows = self._conn.execute(
                """
                SELECT sa.target_sentence, sa.weight, s.weight
                FROM sentence_adj sa
                JOIN sentences s ON s.text = sa.target_sentence
                WHERE sa.source_sentence = ?
                ORDER BY sa.weight DESC
                LIMIT 40
                """,
                (seed_sentence,),
            ).fetchall()

            backward_rows = self._conn.execute(
                """
                SELECT sa.source_sentence, sa.weight, s.weight
                FROM sentence_adj sa
                JOIN sentences s ON s.text = sa.source_sentence
                WHERE sa.target_sentence = ?
                ORDER BY sa.weight DESC
                LIMIT 40
                """,
                (seed_sentence,),
            ).fetchall()

            for sentence, edge_weight, sentence_weight in list(forward_rows) + list(backward_rows):
                sentence_str = str(sentence)
                score = (float(edge_weight) * 0.9) + (float(sentence_weight) * 0.2)
                if q_words:
                    score += _phrase_match_score(sentence_str, q_words) * 0.5
                sentence_scores[sentence_str] += score
                sentence_weights[sentence_str] = int(sentence_weight)


def _normalize_text(text: str) -> str:
    return text.strip().lower()


def _split_sentences(text: str) -> list[str]:
    raw_chunks = _SENTENCE_SPLIT_RE.split(text)
    chunks = [" ".join(chunk.split()) for chunk in raw_chunks if chunk and chunk.strip()]
    if not chunks and text.strip():
        return [text.strip()]
    return chunks


def _split_words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _extract_phrases(words: list[str]) -> list[str]:
    by_n = _extract_phrases_by_n(words)
    return [phrase for n in (2, 3, 4) for phrase in by_n.get(n, [])]


def _extract_phrases_by_n(words: list[str]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {2: [], 3: [], 4: []}
    if len(words) < 2:
        return out

    for n in (2, 3, 4):
        if len(words) < n:
            continue
        for i in range(0, len(words) - n + 1):
            out[n].append(" ".join(words[i : i + n]))
    return out


def _word_match_score(term: str, weight: int, q_words: list[str]) -> float:
    base = math.log1p(max(weight, 0)) * 0.25
    if not q_words:
        return base + 0.5

    best = 0.0
    for q in q_words:
        if term == q:
            best = max(best, 3.2)
            continue
        if q in term or term in q:
            best = max(best, 2.0)
            continue
        ratio = SequenceMatcher(None, term, q).ratio()
        best = max(best, ratio * 1.7)

    return best + base


def _phrase_match_score(text: str, q_words: list[str]) -> float:
    if not q_words:
        return 0.0

    token_set = set(_split_words(text))
    if not token_set:
        return 0.0

    overlap = 0
    fuzzy = 0.0
    for q in q_words:
        if q in token_set:
            overlap += 1
            continue
        fuzzy = max(fuzzy, max((SequenceMatcher(None, q, t).ratio() for t in token_set), default=0.0))

    return (overlap * 1.4) + (fuzzy * 0.8)


def _serve(db: GraphMemoryDB) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        try:
            SOCKET_PATH.unlink()
        except OSError:
            pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)
    server.listen(64)
    server.settimeout(1.0)

    stopping = {"value": False}

    def _stop_handler(signum, frame):  # type: ignore[no-untyped-def]
        stopping["value"] = True

    signal.signal(signal.SIGTERM, _stop_handler)
    signal.signal(signal.SIGINT, _stop_handler)

    try:
        while not stopping["value"]:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                if stopping["value"]:
                    break
                continue

            with conn:
                raw = _read_line(conn)
                if not raw:
                    continue

                try:
                    req = json.loads(raw)
                    resp = _handle_request(db, req)
                except Exception as exc:
                    resp = {"ok": False, "error": str(exc)}

                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
    finally:
        server.close()
        db.close()
        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except OSError:
                pass


def _read_line(conn: socket.socket) -> str:
    chunks = []
    while True:
        data = conn.recv(4096)
        if not data:
            break
        chunks.append(data)
        if b"\n" in data:
            break
    if not chunks:
        return ""
    raw = b"".join(chunks).split(b"\n", 1)[0]
    return raw.decode("utf-8", errors="replace")


def _handle_request(db: GraphMemoryDB, req: dict) -> dict:
    action = str(req.get("action", "")).strip().lower()

    if action == "ping":
        return {"ok": True, "pong": True}

    if action == "ingest":
        text = str(req.get("text", ""))
        result = db.ingest(text)
        return {"ok": True, "result": result}

    if action == "query":
        query = str(req.get("query", ""))
        top_k = int(req.get("top_k", 5))
        top_k = max(1, min(top_k, 20))
        result = db.query(query=query, top_k=top_k)
        return {
            "ok": True,
            "result": {
                "top_words": result.words,
                "top_phrases": result.phrases,
                "top_sentences": result.sentences,
            },
        }

    if action == "remember":
        key = str(req.get("key", "")).strip()
        value = str(req.get("value", ""))
        if not key:
            return {"ok": False, "error": "key is required"}
        db.remember(key, value)
        db.ingest(f"{key} {value}")
        return {"ok": True, "result": "stored"}

    if action == "recall":
        key = str(req.get("key", "")).strip()
        if not key:
            return {"ok": False, "error": "key is required"}
        value = db.recall(key)
        return {"ok": True, "result": value}

    if action == "stats":
        words = db._conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        phrases = db._conn.execute("SELECT COUNT(*) FROM phrases").fetchone()[0]
        sentences = db._conn.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
        return {
            "ok": True,
            "result": {
                "words": int(words),
                "phrases": int(phrases),
                "sentences": int(sentences),
            },
        }

    if action == "clear":
        scope = str(req.get("scope", "all"))
        result = db.clear(scope)
        return {
            "ok": True,
            "result": {
                "scope": scope,
                "cleared_graph_rows": int(result.get("graph_rows", 0)),
                "cleared_kv_rows": int(result.get("kv_rows", 0)),
            },
        }

    return {"ok": False, "error": f"unknown action: {action}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rhea graph memory daemon")
    parser.add_argument("--daemon", action="store_true", help="run in daemon/server mode")
    args = parser.parse_args()

    if not args.daemon:
        print("Use --daemon to run the graph memory server.")
        return

    db = GraphMemoryDB(DB_PATH)
    _serve(db)


if __name__ == "__main__":
    main()

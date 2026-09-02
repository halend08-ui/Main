"""SQLite access layer.

Why SQLite: the workload is a single-writer research pipeline with heavy
analytical reads over a few hundred million rows at most, and reproducibility
matters more than concurrency. The SQL kept here is deliberately close to
portable ANSI so a PostgreSQL backend can be added without rewriting callers
(see ARCHITECTURE.md, "Storage portability").
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from research_engine.core.errors import StorageError
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import utcnow

log = get_logger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1


def _row_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class Database:
    """Thin, thread-safe wrapper around a SQLite connection pool (one per thread)."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 10_000,
                 journal_mode: str = "WAL", synchronous: str = "NORMAL",
                 cache_size_kb: int = 65_536) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._journal_mode = journal_mode
        self._synchronous = synchronous
        self._cache_size_kb = cache_size_kb
        self._local = threading.local()
        self._shared_memory_conn: sqlite3.Connection | None = None
        self._write_lock = threading.RLock()

    # -- connection --------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        if str(self.path) == ":memory:":
            # A single shared connection: separate in-memory connections would
            # each see a different empty database.
            if self._shared_memory_conn is None:
                self._shared_memory_conn = self._connect()
            return self._shared_memory_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=self._busy_timeout_ms / 1000,
                               check_same_thread=False,
                               detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = _row_factory
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
        if str(self.path) != ":memory:":
            conn.execute(f"PRAGMA journal_mode = {self._journal_mode}")
        conn.execute(f"PRAGMA synchronous = {self._synchronous}")
        conn.execute(f"PRAGMA cache_size = -{int(self._cache_size_kb)}")
        conn.execute("PRAGMA temp_store = MEMORY")
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
        if self._shared_memory_conn is not None:
            self._shared_memory_conn.close()
            self._shared_memory_conn = None

    # -- schema ------------------------------------------------------------
    def migrate(self) -> int:
        """Apply the schema idempotently and record the version."""
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.transaction() as conn:
            conn.executescript(sql)
            row = conn.execute(
                "SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            current = (row or {}).get("v") or 0
            if current < SCHEMA_VERSION:
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (SCHEMA_VERSION, "initial", utcnow().isoformat()))
        log.info("database ready", path=str(self.path), version=SCHEMA_VERSION)
        return SCHEMA_VERSION

    def schema_version(self) -> int:
        try:
            row = self.query_one("SELECT MAX(version) AS v FROM schema_migrations")
        except StorageError:
            return 0
        return int((row or {}).get("v") or 0)

    # -- execution ---------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection
        with self._write_lock:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> int:
        try:
            with self.transaction() as conn:
                cur = conn.execute(sql, params)
                return cur.rowcount
        except sqlite3.Error as exc:
            raise StorageError(f"{exc} :: {sql.strip().splitlines()[0]}") from exc

    def execute_many(self, sql: str,
                     rows: Iterable[Sequence[Any] | Mapping[str, Any]]) -> int:
        batch = list(rows)
        if not batch:
            return 0
        try:
            with self.transaction() as conn:
                cur = conn.executemany(sql, batch)
                return cur.rowcount
        except sqlite3.Error as exc:
            raise StorageError(f"{exc} :: {sql.strip().splitlines()[0]}") from exc

    def query(self, sql: str,
              params: Sequence[Any] | Mapping[str, Any] = ()) -> list[dict[str, Any]]:
        try:
            return self.connection.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"{exc} :: {sql.strip().splitlines()[0]}") from exc

    def query_one(self, sql: str,
                  params: Sequence[Any] | Mapping[str, Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> Any:
        row = self.query_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    def insert(self, table: str, values: Mapping[str, Any], *,
               on_conflict: str = "") -> int:
        cols = list(values.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
               f"{on_conflict}")
        try:
            with self.transaction() as conn:
                cur = conn.execute(sql, [values[c] for c in cols])
                return int(cur.lastrowid or 0)
        except sqlite3.Error as exc:
            raise StorageError(f"{exc} :: insert into {table}") from exc

    def upsert(self, table: str, values: Mapping[str, Any], *,
               conflict_columns: Sequence[str],
               update_columns: Sequence[str] | None = None) -> int:
        cols = list(values.keys())
        updates = [c for c in (update_columns if update_columns is not None else cols)
                   if c not in conflict_columns]
        set_clause = ", ".join(f"{c}=excluded.{c}" for c in updates) or \
                     f"{cols[0]}={table}.{cols[0]}"
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)}) "
               f"ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET {set_clause}")
        try:
            with self.transaction() as conn:
                cur = conn.execute(sql, [values[c] for c in cols])
                return int(cur.lastrowid or 0)
        except sqlite3.Error as exc:
            raise StorageError(f"{exc} :: upsert into {table}") from exc

    def upsert_many(self, table: str, rows: Sequence[Mapping[str, Any]], *,
                    conflict_columns: Sequence[str],
                    update_columns: Sequence[str] | None = None) -> int:
        if not rows:
            return 0
        cols = list(rows[0].keys())
        for row in rows:
            if list(row.keys()) != cols:
                raise StorageError("upsert_many requires uniform column ordering")
        updates = [c for c in (update_columns if update_columns is not None else cols)
                   if c not in conflict_columns]
        set_clause = ", ".join(f"{c}=excluded.{c}" for c in updates) or \
                     f"{cols[0]}={table}.{cols[0]}"
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)}) "
               f"ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET {set_clause}")
        return self.execute_many(sql, [[r[c] for c in cols] for r in rows])

    def analyze(self) -> None:
        """Refresh planner statistics; cheap and worth running after big loads."""
        self.execute("ANALYZE")

    def vacuum(self) -> None:
        self.connection.execute("VACUUM")

    def table_counts(self) -> dict[str, int]:
        tables = [r["name"] for r in self.query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: int(self.scalar(f"SELECT COUNT(*) FROM {t}") or 0) for t in tables}


def connect(settings: Any) -> Database:
    """Build a :class:`Database` from :class:`~research_engine.config.Settings`."""
    db = Database(
        settings.database_path,
        busy_timeout_ms=int(settings.get("database.busy_timeout_ms", 10_000)),
        journal_mode=str(settings.get("database.journal_mode", "WAL")),
        synchronous=str(settings.get("database.synchronous", "NORMAL")),
        cache_size_kb=int(settings.get("database.cache_size_kb", 65_536)),
    )
    db.migrate()
    return db


def dumps(payload: Any) -> str:
    """JSON for TEXT columns: stable ordering so diffs and hashes are stable."""
    return json.dumps(payload, sort_keys=True, default=_json_default)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):        # enums
        return value.value
    if hasattr(value, "isoformat"):    # dates
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return str(value)

"""Acceso SQLite con consultas parametrizadas e historial verificable."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core import normalize_text, priority, utc_now, validate_transition

SQLITE_MAX_INT = 2**63 - 1  # los INTEGER de SQLite son de 64 bits con signo

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS tickets (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code TEXT UNIQUE,
 title TEXT NOT NULL CHECK(length(title) BETWEEN 8 AND 100),
 description TEXT NOT NULL CHECK(length(description) BETWEEN 20 AND 700),
 category TEXT NOT NULL,
 location TEXT NOT NULL,
 reporter TEXT NOT NULL,
 impact INTEGER NOT NULL CHECK(impact BETWEEN 1 AND 3),
 urgency INTEGER NOT NULL CHECK(urgency BETWEEN 1 AND 3),
 score INTEGER NOT NULL,
 priority TEXT NOT NULL CHECK(priority IN ('Baja','Media','Alta')),
 status TEXT NOT NULL DEFAULT 'Abierto' CHECK(status IN ('Abierto','En proceso','Resuelto')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
 old_status TEXT,
 new_status TEXT NOT NULL,
 note TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_ticket_priority ON tickets(priority);
CREATE INDEX IF NOT EXISTS idx_event_ticket ON events(ticket_id);
"""


def valid_id(ticket_id) -> bool:
    """Un id fuera del rango de SQLite provocaba OverflowError (conexión cortada)."""
    return isinstance(ticket_id, int) and 0 < ticket_id <= SQLITE_MAX_INT


def like_pattern(term: str) -> str:
    """Convierte texto libre en patrón LIKE literal ('%' y '_' no son comodines)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class Repository:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        """Una transacción y un cierre explícito en cada operación."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_ticket(self, obj):
        label, score = priority(obj["impact"], obj["urgency"])
        now = utc_now()
        with self.connection() as db:
            cur = db.execute("""INSERT INTO tickets
                (title,description,category,location,reporter,impact,urgency,score,priority,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,'Abierto',?,?)""",
                (obj["title"], obj["description"], obj["category"], obj["location"],
                 obj["reporter"], obj["impact"], obj["urgency"], score, label, now, now))
            ticket_id = cur.lastrowid
            code = f"INC-{datetime.now(timezone.utc).year}-{ticket_id:04d}"
            db.execute("UPDATE tickets SET code=? WHERE id=?", (code, ticket_id))
            db.execute("INSERT INTO events(ticket_id,old_status,new_status,note,created_at) VALUES (?,NULL,'Abierto',?,?)",
                       (ticket_id, "Reporte registrado en la bitácora.", now))
        return ticket_id

    def get_ticket(self, ticket_id):
        if not valid_id(ticket_id):
            return None
        with self.connection() as db:
            ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            return dict(ticket) if ticket else None

    def list_tickets(self, q="", status=""):
        sql = "SELECT * FROM tickets WHERE 1=1"
        params = []
        if q:
            sql += (" AND (code LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\'"
                    " OR location LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\')")
            params += [like_pattern(q[:90])] * 4
        if status in ("Abierto", "En proceso", "Resuelto"):
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY CASE priority WHEN 'Alta' THEN 0 WHEN 'Media' THEN 1 ELSE 2 END, id DESC"
        with self.connection() as db:
            return [dict(x) for x in db.execute(sql, params).fetchall()]

    def get_events(self, ticket_id):
        if not valid_id(ticket_id):
            return []
        with self.connection() as db:
            return [dict(x) for x in db.execute("SELECT * FROM events WHERE ticket_id=? ORDER BY id DESC", (ticket_id,))]

    def change_status(self, ticket_id, next_state, note):
        if not valid_id(ticket_id):
            raise LookupError("No existe la incidencia")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT status FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if old is None:
                raise LookupError("No existe la incidencia")
            errors = validate_transition(old["status"], next_state, note)
            if errors:
                raise ValueError(" ".join(errors))
            now = utc_now()
            db.execute("UPDATE tickets SET status=?,updated_at=? WHERE id=?", (next_state, now, ticket_id))
            db.execute("INSERT INTO events(ticket_id,old_status,new_status,note,created_at) VALUES (?,?,?,?,?)",
                       (ticket_id, old["status"], next_state, normalize_text(note), now))

    def metrics(self):
        with self.connection() as db:
            counts = {x["status"]: x["count"] for x in db.execute("SELECT status,COUNT(*) count FROM tickets GROUP BY status")}
            total = db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
            high = db.execute("SELECT COUNT(*) FROM tickets WHERE priority='Alta' AND status!='Resuelto'").fetchone()[0]
        return {"total": total, "open": counts.get("Abierto", 0), "progress": counts.get("En proceso", 0),
                "resolved": counts.get("Resuelto", 0), "high_pending": high}

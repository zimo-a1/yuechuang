"""Versioned SQLite snapshots. One API process owns this local Demo database."""

import fcntl
import json
import sqlite3
import threading
from pathlib import Path


class StateStore:
    def __init__(self, path):
        path = Path(path)
        self.db_path = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._owner = open(str(path) + '.lock', 'a')
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._owner.close()
            raise RuntimeError('任务数据库已被使用；Demo 后端只支持一个进程') from None
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            self.close()
            raise RuntimeError('不支持的任务数据库版本')
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            self.db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                payload TEXT NOT NULL)''')
            self.db.execute('CREATE INDEX IF NOT EXISTS jobs_session ON jobs(session_id)')
            self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self.db.execute('''CREATE TABLE IF NOT EXISTS video_usage (
                id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
                episode INTEGER NOT NULL, segment_id TEXT NOT NULL, day TEXT NOT NULL,
                seconds INTEGER NOT NULL, regeneration INTEGER NOT NULL,
                UNIQUE(task_id, segment_id))''')
            self.db.execute('CREATE INDEX IF NOT EXISTS video_usage_day ON video_usage(day)')
            self.db.execute('CREATE INDEX IF NOT EXISTS video_usage_session ON video_usage(session_id, episode)')
            self.db.execute('PRAGMA user_version=2')

    def close(self):
        with self._lock:
            self.db.close()
            self._owner.close()

    def import_legacy(self, records):
        """Import JSON snapshots that predate or were written outside this SQLite store.

        A session deletion removes its JSON snapshot, so importing only snapshots
        that still exist cannot resurrect a deleted session.  Rechecking on every
        process start also recovers a session written by an older backend process
        after this database's initial migration had already completed.
        """
        with self._lock, self.db:
            for record in records:
                tombstone = f"deleted_session:{record['session_id']}"
                if self.db.execute('SELECT 1 FROM metadata WHERE key=?', (tombstone,)).fetchone():
                    continue
                self.db.execute('INSERT OR IGNORE INTO sessions VALUES (?, ?)',
                                (record['session_id'], json.dumps(record, ensure_ascii=False)))
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('legacy_imported', '1')")

    def sessions(self):
        with self._lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM sessions')]

    def save_session(self, record):
        with self._lock, self.db:
            self.db.execute('INSERT INTO sessions VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                            (record['session_id'], json.dumps(record, ensure_ascii=False)))
            self.db.execute('DELETE FROM metadata WHERE key=?', (f"deleted_session:{record['session_id']}",))

    def delete_session(self, session_id):
        with self._lock, self.db:
            self.db.execute('DELETE FROM sessions WHERE id=?', (session_id,))
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)',
                            (f"deleted_session:{session_id}", '1'))

    def save_job(self, job):
        with self._lock, self.db:
            self.db.execute('INSERT INTO jobs VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                            (job['task_id'], job['session_id'], json.dumps(job, ensure_ascii=False)))

    def jobs(self, session_id=None):
        with self._lock:
            if session_id is None:
                rows = self.db.execute('SELECT payload FROM jobs').fetchall()
            else:
                rows = self.db.execute('SELECT payload FROM jobs WHERE session_id=?', (session_id,)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def get_job(self, task_id):
        with self._lock:
            row = self.db.execute('SELECT payload FROM jobs WHERE id=?', (task_id,)).fetchone()
            return json.loads(row[0]) if row else None

"""Persist AG-UI history outside the shared Agent, as the web client does."""

import json
import sqlite3
from pathlib import Path

from redteam_bridge.errors import BridgeError


class ConversationStore:
    def __init__(self, path: Path, owner: str) -> None:
        self.path = path
        self.owner = owner
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS conversations "
                "(id TEXT PRIMARY KEY, owner TEXT NOT NULL, data TEXT NOT NULL)"
            )

    def load(self, conversation_id: str) -> dict:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT data FROM conversations WHERE id = ? AND owner = ?",
                (conversation_id, self.owner),
            ).fetchone()
        if row is None:
            raise BridgeError(404, "conversation_not_found", "Start a new conversation without conversation_id.")
        return json.loads(row[0])

    def save(self, conversation_id: str, data: dict) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO conversations (id, owner, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (conversation_id, self.owner, json.dumps(data)),
            )

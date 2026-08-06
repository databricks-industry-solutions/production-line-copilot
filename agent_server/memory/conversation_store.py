"""Conversation persistence for agent chat history.

Stores conversations and messages in Postgres (Lakebase) when available.
Falls back to in-memory storage when Postgres is not configured.
"""
import json
import logging
import uuid
from collections import defaultdict

from agent_server.memory.pg_pool import query, transaction

logger = logging.getLogger(__name__)

_in_memory_conversations: dict[str, list[dict]] = defaultdict(list)


class ConversationStore:
    """Manages conversations and messages with graceful PG fallback."""

    def create_conversation(self, shift_id: str) -> str:
        conv_id = str(uuid.uuid4())
        try:
            with transaction() as cur:
                if cur is not None:
                    cur.execute(
                        "INSERT INTO ai_chatbot.conversations (conversation_id, shift_id) VALUES (%s, %s)",
                        (conv_id, shift_id),
                    )
        except Exception:
            logger.warning("PG conversation create failed, using in-memory")
        return conv_id

    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        msg = {
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }

        try:
            with transaction() as cur:
                if cur is not None:
                    cur.execute(
                        "INSERT INTO ai_chatbot.messages (conversation_id, role, content, metadata) "
                        "VALUES (%s, %s, %s, %s)",
                        (conversation_id, role, content, json.dumps(metadata or {})),
                    )
                    return
        except Exception:
            logger.warning("PG message save failed, using in-memory")

        _in_memory_conversations[conversation_id].append(msg)

    def load_history(self, conversation_id: str, limit: int = 50) -> list[dict]:
        """Return the most recent ``limit`` messages, oldest first.

        The ordering matters more than it looks. Callers ask for a small window
        (six) to prime the model with what was just said, and this used to be
        ``ORDER BY created_at LIMIT %s`` — the *oldest* six. For the first three
        turns that is the same set, which is exactly long enough to look
        correct; from the fourth turn on the window froze on the opening
        exchange and the model was primed with progressively staler context
        while newer turns were never shown to it.

        Selecting DESC and reversing keeps the returned list chronological,
        which callers rely on: they treat the final element as the message just
        saved for this turn and drop it.
        """
        try:
            rows = query(
                "SELECT role, content, metadata, created_at FROM ai_chatbot.messages "
                "WHERE conversation_id = %s ORDER BY created_at DESC LIMIT %s",
                (conversation_id, limit),
            )
            if rows is not None:
                return list(reversed(rows))
        except Exception:
            # ``query`` has already rolled the shared connection back; the old
            # code did not, so a bad history read poisoned every later caller.
            logger.warning("PG history load failed, using in-memory")

        return _in_memory_conversations.get(conversation_id, [])[-limit:]

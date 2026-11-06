from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger("heor_signal.telegram")

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


def _call(token: str, method: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
    response = requests.post(API.format(token=token, method=method), json=payload, timeout=timeout)
    try:
        data = response.json()
    except Exception:
        raise TelegramError(f"Telegram returned HTTP {response.status_code}") from None
    if not data.get("ok"):
        description = data.get("description") or f"HTTP {response.status_code}"
        retry_after = (data.get("parameters") or {}).get("retry_after")
        if retry_after:
            log.warning("Telegram rate limit: waiting %ss", retry_after)
            time.sleep(min(20, int(retry_after)) + 1)
            return _call(token, method, payload, timeout=timeout)
        raise TelegramError(description)
    return data


def send_message(token: str, chat_id: str, text: str) -> dict[str, Any]:
    return _call(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "disable_notification": False,
        },
    )


def send_messages(token: str, chat_id: str, messages: list[str]) -> int:
    sent = 0
    for message in messages:
        if not message.strip():
            continue
        send_message(token, chat_id, message)
        sent += 1
        if sent < len(messages):
            time.sleep(1.1)  # stay under Telegram's per-chat rate limit
    return sent


def send_test(token: str, chat_id: str, app_name: str = "HEOR Signal") -> dict[str, Any]:
    text = (
        f"{app_name} is connected.\n\n"
        "You will receive the daily read on evidence, access and policy here.\n"
        "The dashboard remains the full archive."
    )
    return send_message(token, chat_id, text)


def discover_chats(token: str) -> list[dict[str, Any]]:
    data = _call(token, "getUpdates", {"limit": 100, "timeout": 0})
    chats: dict[str, dict[str, Any]] = {}
    for update in data.get("result", []):
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("my_chat_member")
            or {}
        )
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        key = str(chat_id)
        name = (
            chat.get("title")
            or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            or chat.get("username")
            or key
        )
        chats[key] = {
            "chat_id": key,
            "name": name,
            "type": chat.get("type", ""),
            "username": chat.get("username", ""),
            "last_text": (message.get("text") or "")[:120],
        }
    return list(chats.values())

"""Safe Telegram notifications for explicit application events.

This module deliberately has no keyboard, clipboard, or input-monitoring code.
Credentials are read from environment variables first and then from config.json.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

import requests

from config import load_config


def _setting(config: Mapping[str, Any], key: str, env_key: str) -> str:
    """Read a Telegram setting without exposing its value in logs or errors."""
    return str(os.getenv(env_key) or config.get(key) or "").strip()


def Send_tele_msg(
    message: str,
    config: Optional[Mapping[str, Any]] = None,
    timeout: float = 10.0,
) -> bool:
    """Send an explicit application notification to Telegram.

    Configure either:

    - ``MOHRA_TELEGRAM_BOT_TOKEN`` and ``MOHRA_TELEGRAM_CHAT_ID`` environment
      variables (recommended), or
    - ``telegram_bot_token`` and ``telegram_chat_id`` in ``config.json``.

    Returns ``True`` only when Telegram reports a successful request. It never
    raises for missing configuration or network/API failures.
    """
    text = str(message).strip()
    if not text:
        return False

    cfg = config if config is not None else load_config()
    token = _setting(cfg, "telegram_bot_token", "MOHRA_TELEGRAM_BOT_TOKEN")
    chat_id = _setting(cfg, "telegram_chat_id", "MOHRA_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        result = response.json()
        return bool(result.get("ok"))
    except (requests.RequestException, ValueError, TypeError):
        return False


__all__ = ["Send_tele_msg"]

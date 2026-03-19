from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from config import Settings

if TYPE_CHECKING:
    from langfuse import Langfuse


logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _build_langfuse(
    public_key: str,
    secret_key: str,
    host: str,
) -> "Langfuse | None":
    if not public_key or not secret_key:
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
    except ImportError:
        logger.warning("langfuse not installed; tracing disabled")
        return None


def get_langfuse(settings: Settings) -> "Langfuse | None":
    return _build_langfuse(
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
        settings.langfuse_base_url,
    )

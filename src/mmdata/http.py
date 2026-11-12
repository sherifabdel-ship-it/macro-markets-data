"""HTTP helper. Retries transient failures and NEVER lets an API key appear in an error message."""
from __future__ import annotations

import time

import requests


class SourceError(Exception):
    pass


def redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def get_json(url: str, params: dict, secrets: tuple = (), tries: int = 4, timeout: int = 30):
    last: Exception | None = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:  # message can contain the full URL, so redact it
            last = SourceError(redact(f"network error: {type(e).__name__}: {e}", *secrets))
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise SourceError("response was not valid JSON") from None
            if r.status_code == 429 or r.status_code >= 500:
                last = SourceError(f"HTTP {r.status_code} (temporary)")
            else:
                detail = ""
                try:
                    detail = str(r.json().get("error_message", ""))
                except Exception:
                    pass
                raise SourceError(redact(f"HTTP {r.status_code} {detail}".strip(), *secrets))
        if i < tries - 1:
            time.sleep(min(2 ** i, 8))
    raise last  # type: ignore[misc]

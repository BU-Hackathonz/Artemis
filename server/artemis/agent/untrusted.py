"""Wrap text that came from files or programs so the model treats it as data (design B4)."""

MAX_BYTES = 8 * 1024


def truncate(text: str, limit: int = MAX_BYTES) -> str:
    """Keep the head and the tail; say how much was cut."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    half = limit // 2
    head = raw[:half].decode("utf-8", errors="ignore")
    tail = raw[-half:].decode("utf-8", errors="ignore")
    return f"{head}\n[… {len(raw) - 2 * half} bytes cut …]\n{tail}"


def wrap(source: str, text: str, limit: int = MAX_BYTES) -> str:
    """`<untrusted source="file:main.py">…</untrusted>`; the text can't close the tag itself."""
    body = truncate(text, limit).replace("</untrusted", "<\\/untrusted")
    label = source.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<untrusted source="{label}">\n{body}\n</untrusted>'

"""Diagnostics the client reports so the core can speak them (contracts.md §1).

Only fixed codes, never free text: the core turns a code into a fixed sentence, so nothing the
client (or a page in its webview) sends is ever spoken verbatim.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DiagnosticCode = Literal["shortcut_registration_failed"]


class ClientDiagnostic(BaseModel):
    """Body of POST /sessions/{sid}/diagnostics."""

    model_config = ConfigDict(extra="forbid")

    code: DiagnosticCode
    # The chord that failed, e.g. "Control+Alt+Space": letters, digits and "+" only.
    shortcut: str | None = Field(default=None, pattern=r"^[A-Za-z0-9+]{1,40}$")

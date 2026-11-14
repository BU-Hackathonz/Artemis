"""What the client reports so the core can speak it (contracts.md §1): diagnostics and readiness.

Only fixed codes, never free text: the core turns a code into a fixed sentence, so nothing the
client (or a page in its webview) sends is ever spoken verbatim.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DiagnosticCode = Literal["shortcut_registration_failed"]

# A key chord such as "Control+Shift+Space": letters, digits and "+" only.
CHORD_PATTERN = r"^[A-Za-z0-9+]{1,40}$"


class ClientDiagnostic(BaseModel):
    """Body of POST /sessions/{sid}/diagnostics."""

    model_config = ConfigDict(extra="forbid")

    code: DiagnosticCode
    # The chord that failed, e.g. "Control+Alt+Space": letters, digits and "+" only.
    shortcut: str | None = Field(default=None, pattern=CHORD_PATTERN)


class ClientReady(BaseModel):
    """Body of POST /sessions/{sid}/ready: the client finished setting up for this session."""

    model_config = ConfigDict(extra="forbid")

    # The push-to-talk chord the client registered, e.g. "Control+Shift+Space"; None when
    # registration failed (the client also reports that as a diagnostic). Only the native app
    # calls /ready: a browser has no Start listening button, which the no-chord line names.
    shortcut: str | None = Field(default=None, pattern=CHORD_PATTERN)

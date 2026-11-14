"""Conversation (David): what to do with something the developer said.

Deterministic and pure: no model, no audio, no HTTP. The core hands every final transcript to
`Controller.decide()` and does what the `Decision` says; local phrases ("stop talking", "what
are you doing") are answered here, and everything else goes to the agent word for word.
"""

from .controller import Controller, Decision, Style
from .intents import INTENTS, match, simplify

__all__ = ["INTENTS", "Controller", "Decision", "Style", "match", "simplify"]

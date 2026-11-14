"""Runner (Avery, infrastructure; contracts.md §3.4): project folders, file writes, restricted runs.

"Gated and limited", not "sandboxed": every write and run was approved by the user and scanned by
the gateway before it gets here. The run itself adds a separate interpreter in isolated mode, an
explicit minimal environment (no API keys), no stdin, a timeout, a whole-process-tree kill and an
output cap.
"""

from .local import LocalRunner, NoInterpreter, find_python

__all__ = ["LocalRunner", "NoInterpreter", "find_python"]

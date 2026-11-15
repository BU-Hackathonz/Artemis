"""Echo Check (Avery + Jun): the real accessibility tree and axe-core findings for a local page, spoken."""

from .check import EchoError, EchoReport, Finding, compare, describe_page, echo_check, speak_report

__all__ = ["EchoError", "EchoReport", "Finding", "compare", "describe_page", "echo_check", "speak_report"]

"""Shared test settings for the whole server package."""

import pytest


@pytest.fixture(autouse=True)
def _no_ring_lead(monkeypatch):
    """The gate waits RING_LEAD_S for the ring before opening or typing (policy/desktop.py). Tests don't
    watch a ring, so they don't wait; test_ring_lead sets it back on purpose."""
    monkeypatch.setattr("artemis.policy.desktop.RING_LEAD_S", 0)

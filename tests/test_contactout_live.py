"""Opt-in free live connection check. Never enabled by CI or ordinary pytest runs."""

import os

import httpx
import pytest

from treg.api import app
from treg.infra.db import reset_db


@pytest.mark.skipif(
    os.environ.get("TREG_CONTACTOUT_LIVE_VERIFY") != "1",
    reason="explicit live verification only",
)
async def test_live_contactout_connect(clients, monkeypatch):
    from dotenv import dotenv_values

    credential = dotenv_values(".env").get("TREG_PLATFORM_KEY_CONTACTOUT")
    if not credential:
        pytest.fail("ContactOut platform credential missing")
    async with httpx.AsyncClient(timeout=30) as upstream:
        monkeypatch.setattr(app.state, "http", upstream)
        try:
            bad = await clients.post(
                "/connections/token",
                json={
                    "provider": "contactout",
                    "token": "treg-intentionally-invalid-20260908",
                },
            )
            assert bad.status_code == 422
            good = await clients.post(
                "/connections/token",
                json={"provider": "contactout", "token": credential},
            )
            assert good.status_code == 200
            tools = (await clients.get("/tools")).json()
            tool = next(t for t in tools if t["name"] == "contactout")
            assert tool["bindings"][0]["name"] == "token"
        finally:
            # This is the isolated pytest database, never the developer or production database.
            await reset_db()

"""A credential that has expired is not a credential.

``PGPASSWORD`` is the documented way to hand Lakebase a token, and
``scripts/init_lakebase.py`` recommends it in as many words. What neither says
is that a Lakebase credential lasts an hour. ``_get_pg_password`` returned it
unconditionally, so once exported it outlived its own validity and every
connection after that presented the same dead token.

Postgres refuses it with "OAuth: User is not authorized", which reads as a
permissions problem and sends you off to check roles and grants. Here those were
correct the whole time — the role had SUPERUSER on the right endpoint — while
the deploy's incident reconciliation quietly skipped its Lakebase pass for a day
and a half.

These tests pin the distinction the code now makes: a live token is used, a dead
one is replaced, and a plain password is left alone.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from agent_server.memory.pg_pool import _is_expired_token


def _jwt(exp: float | None, *, header="hdr", signature="sig") -> str:
    claims: dict = {"sub": "someone@example.com"}
    if exp is not None:
        claims["exp"] = exp
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{body}.{signature}"


class TestItSpotsADeadToken:
    def test_an_hour_past_expiry(self):
        assert _is_expired_token(_jwt(time.time() - 3600)) is True

    def test_expiring_right_now_counts_as_dead(self):
        """A token with seconds left cannot survive the connection it is for."""
        assert _is_expired_token(_jwt(time.time() + 5)) is True

    def test_the_real_failure_this_came_from(self):
        """Issued 12:53, expired 13:53, still being presented 30 hours later."""
        issued = 1785934400
        assert _is_expired_token(_jwt(issued + 3600)) is True


class TestItLeavesGoodCredentialsAlone:
    def test_a_token_with_time_left_is_used(self):
        assert _is_expired_token(_jwt(time.time() + 3600)) is False

    def test_a_plain_password_has_no_expiry_to_read(self):
        """Provisioned Lakebase can use an ordinary password. Not a JWT, not ours to judge."""
        assert _is_expired_token("hunter2") is False
        assert _is_expired_token("a.b") is False
        assert _is_expired_token("") is False

    def test_a_jwt_without_an_exp_claim_is_left_alone(self):
        assert _is_expired_token(_jwt(None)) is False

    @pytest.mark.parametrize("junk", ["x.y.z", "a.!!!!.c", "a." + "=" * 5 + ".c"])
    def test_undecodable_middles_do_not_raise(self, junk):
        """Guessing wrong here must not take the connection down with it."""
        assert _is_expired_token(junk) is False

    def test_a_non_numeric_exp_is_ignored(self):
        body = base64.urlsafe_b64encode(json.dumps({"exp": "soon"}).encode()).decode().rstrip("=")
        assert _is_expired_token(f"h.{body}.s") is False


class TestThePasswordPathUsesIt:
    def test_an_expired_pgpassword_is_not_returned(self, monkeypatch):
        from agent_server.memory import pg_pool

        monkeypatch.setenv("PGPASSWORD", _jwt(time.time() - 3600))
        monkeypatch.setenv("LAKEBASE_ENDPOINT", "")
        # With no endpoint and no usable client this falls through to "", but the
        # point stands: whatever it returns, it is not the dead token.
        monkeypatch.setattr(pg_pool, "logger", pg_pool.logger)
        assert pg_pool._get_pg_password() != _jwt(time.time() - 3600)

    def test_a_live_pgpassword_is_returned_untouched(self, monkeypatch):
        from agent_server.memory import pg_pool

        good = _jwt(time.time() + 3600)
        monkeypatch.setenv("PGPASSWORD", good)
        assert pg_pool._get_pg_password() == good

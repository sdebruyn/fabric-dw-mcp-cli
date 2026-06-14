"""Tests for fabric_dw.exceptions.

Covers:
- FabricError keyword-only context attributes (status, request_id, body, hint)
- __str__ with/without hint and request_id
- Every concrete subclass instantiates correctly
- ConfigError factory classmethods
"""

from __future__ import annotations

import pytest

from fabric_dw.exceptions import (
    AlreadyExistsError,
    AuthError,
    ConfigError,
    FabricError,
    FabricServerError,
    ItemKindError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
)

# ---------------------------------------------------------------------------
# FabricError — base class
# ---------------------------------------------------------------------------


class TestFabricErrorBasic:
    """FabricError message and kw-only context attributes."""

    def test_message_only(self) -> None:
        err = FabricError("something went wrong")
        assert str(err) == "something went wrong"

    def test_default_attributes_are_none(self) -> None:
        err = FabricError("msg")
        assert err.status is None
        assert err.request_id is None
        assert err.body is None
        assert err.hint is None

    def test_is_exception(self) -> None:
        err = FabricError("msg")
        assert isinstance(err, Exception)

    def test_kwargs_stored(self) -> None:
        body: dict[str, object] = {"error": {"code": "ItemNotFound"}}
        err = FabricError(
            "not found",
            status=404,
            request_id="req-abc-123",
            body=body,
            hint="Check that the item exists.",
        )
        assert err.status == 404
        assert err.request_id == "req-abc-123"
        assert err.body == body
        assert err.hint == "Check that the item exists."

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(FabricError, match="something went wrong"):
            raise FabricError("something went wrong")


# ---------------------------------------------------------------------------
# FabricError.__str__ formatting
# ---------------------------------------------------------------------------


class TestFabricErrorStr:
    """__str__ appends hint and request_id in the documented order."""

    def test_no_hint_no_request_id(self) -> None:
        err = FabricError("base message")
        assert str(err) == "base message"

    def test_hint_only(self) -> None:
        err = FabricError("base message", hint="Try again later.")
        result = str(err)
        assert "base message" in result
        assert "Hint: Try again later." in result
        # Hint appears after message.
        assert result.index("base message") < result.index("Hint:")

    def test_request_id_only(self) -> None:
        err = FabricError("base message", request_id="req-xyz")
        result = str(err)
        assert "base message" in result
        assert "request-id: req-xyz" in result

    def test_hint_and_request_id(self) -> None:
        err = FabricError(
            "base message",
            hint="Renew your token.",
            request_id="req-abc",
        )
        result = str(err)
        assert "base message" in result
        assert "Hint: Renew your token." in result
        assert "request-id: req-abc" in result

    def test_hint_before_request_id(self) -> None:
        """The implementation appends hint first, then request_id."""
        err = FabricError("msg", hint="h", request_id="r")
        result = str(err)
        assert result.index("Hint:") < result.index("request-id:")

    @pytest.mark.parametrize(
        ("hint", "request_id", "expected_fragment"),
        [
            (None, None, "only message"),
            ("Fix X.", None, "Hint: Fix X."),
            (None, "req-1", "request-id: req-1"),
            ("Fix X.", "req-1", "Hint: Fix X."),
        ],
        ids=["no_extras", "hint_only", "request_id_only", "both"],
    )
    def test_str_parametric(
        self, hint: str | None, request_id: str | None, expected_fragment: str
    ) -> None:
        err = FabricError("only message", hint=hint, request_id=request_id)
        assert expected_fragment in str(err)


# ---------------------------------------------------------------------------
# Subclasses
# ---------------------------------------------------------------------------


class TestSubclasses:
    """Every concrete subclass is a FabricError, accepts the same kw args, and can be raised."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            AuthError,
            PermissionDeniedError,
            NotFoundError,
            RateLimitedError,
            FabricServerError,
            AlreadyExistsError,
            ItemKindError,
        ],
    )
    def test_is_fabric_error(self, exc_cls: type[FabricError]) -> None:
        err = exc_cls("msg")
        assert isinstance(err, FabricError)

    @pytest.mark.parametrize(
        "exc_cls",
        [
            AuthError,
            PermissionDeniedError,
            NotFoundError,
            RateLimitedError,
            FabricServerError,
            AlreadyExistsError,
            ItemKindError,
        ],
    )
    def test_accepts_kwargs(self, exc_cls: type[FabricError]) -> None:
        err = exc_cls(
            "msg",
            status=400,
            request_id="r",
            body={"x": 1},
            hint="h",
        )
        assert err.status == 400
        assert err.request_id == "r"
        assert err.hint == "h"

    @pytest.mark.parametrize(
        "exc_cls",
        [
            AuthError,
            PermissionDeniedError,
            NotFoundError,
            RateLimitedError,
            FabricServerError,
            AlreadyExistsError,
            ItemKindError,
        ],
    )
    def test_can_be_raised_and_caught_as_fabric_error(self, exc_cls: type[FabricError]) -> None:
        with pytest.raises(FabricError):
            raise exc_cls("subclass error")

    @pytest.mark.parametrize(
        "exc_cls",
        [
            AuthError,
            PermissionDeniedError,
            NotFoundError,
            RateLimitedError,
            FabricServerError,
            AlreadyExistsError,
            ItemKindError,
        ],
    )
    def test_str_inherits_hint_formatting(self, exc_cls: type[FabricError]) -> None:
        err = exc_cls("msg", hint="fix it")
        assert "Hint: fix it" in str(err)


# ---------------------------------------------------------------------------
# ConfigError factory classmethods
# ---------------------------------------------------------------------------


class TestConfigError:
    """ConfigError and its factory classmethods."""

    def test_missing_env_vars_single(self) -> None:
        err = ConfigError.missing_env_vars(["FABRIC_CLIENT_SECRET"])
        assert "FABRIC_CLIENT_SECRET" in str(err)
        assert isinstance(err, Exception)

    def test_missing_env_vars_multiple(self) -> None:
        names = ["FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET"]
        err = ConfigError.missing_env_vars(names)
        msg = str(err)
        for name in names:
            assert name in msg

    def test_missing_env_vars_mentions_service_principal(self) -> None:
        err = ConfigError.missing_env_vars(["FABRIC_TENANT_ID"])
        assert "service principal" in str(err).lower()

    def test_unknown_credential_mode(self) -> None:
        err = ConfigError.unknown_credential_mode("bogus-mode")
        assert "bogus-mode" in str(err)
        assert isinstance(err, Exception)

    def test_unknown_credential_mode_repr(self) -> None:
        """The mode should appear in repr form (quotes) in the message."""
        err = ConfigError.unknown_credential_mode("weird")
        assert "'weird'" in str(err) or "weird" in str(err)

    def test_config_error_is_not_fabric_error(self) -> None:
        """ConfigError must NOT be a subtype of FabricError (C22).

        A local config error has no HTTP context; mixing it with FabricError
        causes broad ``except FabricError`` handlers to silently swallow
        configuration problems.
        """
        err = ConfigError("cfg problem")
        assert isinstance(err, Exception)
        assert not isinstance(err, FabricError)

    def test_config_error_is_exception(self) -> None:
        err = ConfigError("cfg problem")
        assert isinstance(err, Exception)

    def test_config_error_message_preserved(self) -> None:
        err = ConfigError("cfg problem")
        assert "cfg problem" in str(err)

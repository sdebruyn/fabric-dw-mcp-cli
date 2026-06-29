"""Unit tests for validate_principal_name and quote_principal in identifiers.py.

These specifically test the broader principal-name validator which accepts Entra
UPNs, application GUIDs, and display names with spaces -- characters that the
stricter validate_identifier rejects.
"""

from __future__ import annotations

import pytest

from fabric_dw.identifiers import quote_principal, validate_principal_name


class TestValidatePrincipalNameAccepted:
    """Valid principal names that must pass validation."""

    def test_entra_upn(self) -> None:
        assert validate_principal_name("alice@contoso.com") == "alice@contoso.com"

    def test_entra_upn_with_subdomain(self) -> None:
        assert validate_principal_name("bob@sales.contoso.com") == "bob@sales.contoso.com"

    def test_guid_format(self) -> None:
        guid = "12345678-1234-1234-1234-1234567890ab"
        assert validate_principal_name(guid) == guid

    def test_role_name_no_spaces(self) -> None:
        assert validate_principal_name("db_owner") == "db_owner"

    def test_role_name_with_spaces(self) -> None:
        assert validate_principal_name("Sales Analysts") == "Sales Analysts"

    def test_single_character_name(self) -> None:
        assert validate_principal_name("a") == "a"

    def test_max_length_128(self) -> None:
        name = "a" * 128
        assert validate_principal_name(name) == name

    def test_name_with_hyphen(self) -> None:
        assert validate_principal_name("my-service-principal") == "my-service-principal"

    def test_name_with_dot(self) -> None:
        assert validate_principal_name("John.Smith") == "John.Smith"

    def test_mixed_case(self) -> None:
        assert validate_principal_name("Alice@Contoso.COM") == "Alice@Contoso.COM"

    def test_digits_in_name(self) -> None:
        assert validate_principal_name("user123@contoso.com") == "user123@contoso.com"

    def test_leading_trailing_whitespace_ok(self) -> None:
        """Leading/trailing spaces are stripped for regex match, name returned as-is."""
        result = validate_principal_name("  alice@contoso.com  ")
        assert result == "  alice@contoso.com  "

    def test_underscores_allowed(self) -> None:
        assert validate_principal_name("my_app_principal") == "my_app_principal"


class TestValidatePrincipalNameRejected:
    """Dangerous or invalid principal names that must be rejected."""

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            validate_principal_name("")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            validate_principal_name("   ")

    def test_closing_bracket_rejected(self) -> None:
        with pytest.raises(ValueError, match="forbidden character"):
            validate_principal_name("a]b")

    def test_semicolon_rejected(self) -> None:
        with pytest.raises(ValueError, match="forbidden character"):
            validate_principal_name("alice;DROP TABLE dbo.t--")

    def test_line_comment_rejected(self) -> None:
        with pytest.raises(ValueError, match="forbidden character"):
            validate_principal_name("alice--comment")

    def test_closing_bracket_injection_attempt(self) -> None:
        with pytest.raises(ValueError, match="forbidden character"):
            validate_principal_name("a];DROP TABLE dbo.t--")

    def test_null_byte_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_principal_name("alice\x00@contoso.com")

    def test_newline_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_principal_name("alice\n@contoso.com")

    def test_tab_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_principal_name("alice\t@contoso.com")

    def test_del_char_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_principal_name("alice\x7f@contoso.com")

    def test_overlong_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="allowed characters"):
            validate_principal_name("a" * 129)

    def test_backslash_rejected(self) -> None:
        with pytest.raises(ValueError, match="allowed characters"):
            validate_principal_name("domain\\user")

    def test_single_quote_rejected(self) -> None:
        with pytest.raises(ValueError, match="allowed characters"):
            validate_principal_name("alice'@contoso.com")

    def test_angle_bracket_rejected(self) -> None:
        with pytest.raises(ValueError, match="allowed characters"):
            validate_principal_name("<script>")

    def test_opening_bracket_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"forbidden character|allowed characters"):
            validate_principal_name("alice[admin]")


class TestQuotePrincipal:
    """quote_principal wraps names in bracket-quotes and escapes ] as ]]."""

    def test_plain_upn(self) -> None:
        assert quote_principal("alice@contoso.com") == "[alice@contoso.com]"

    def test_guid_quoted(self) -> None:
        guid = "12345678-1234-1234-1234-1234567890ab"
        assert quote_principal(guid) == f"[{guid}]"

    def test_name_with_spaces(self) -> None:
        assert quote_principal("Sales Analysts") == "[Sales Analysts]"

    def test_strips_surrounding_whitespace_in_output(self) -> None:
        """Leading/trailing whitespace is stripped from the quoted output."""
        assert quote_principal("  alice@contoso.com  ") == "[alice@contoso.com]"

    def test_bracket_in_name_escaped(self) -> None:
        """A ] in the name is escaped to ]] inside the outer brackets."""
        result = quote_principal("alice]admin")
        assert result == "[alice]]admin]"

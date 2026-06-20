"""Unit tests for the self-contained HTML renderer (_plan_html).

All tests run offline — no Fabric API calls, no network access.  The
vendored html-query-plan assets are loaded from the package as shipped.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fabric_dw.cli._plan_html import render_plan_html

_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"

_FIXTURE_XML = (
    f'<?xml version="1.0" encoding="utf-16"?>'
    f'<ShowPlanXML xmlns="{_NS}" Version="1.6" Build="16.0.0.0">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="SELECT 1" StatementId="1">'
    f"<QueryPlan>"
    f'<RelOp NodeId="0" PhysicalOp="Clustered Index Scan"'
    f' LogicalOp="Clustered Index Scan"'
    f' EstimateRows="1000" EstimatedTotalSubtreeCost="0.5" Parallel="0">'
    f'<IndexScan Ordered="false"/>'
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)

_UNKNOWN_OP_XML = (
    f'<ShowPlanXML xmlns="{_NS}">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="SELECT 1" StatementId="1">'
    f"<QueryPlan>"
    f'<RelOp NodeId="0" PhysicalOp="FabricDistributedShuffle"'
    f' EstimateRows="100" EstimatedTotalSubtreeCost="0.1">'
    f"<GenericOp/>"
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)


class TestRenderPlanHtmlStructure:
    """Verify the structural invariants of the generated HTML document."""

    def test_returns_string(self) -> None:
        """render_plan_html returns a str, not bytes."""
        result = render_plan_html(_FIXTURE_XML)
        assert isinstance(result, str)

    def test_starts_with_doctype(self) -> None:
        """Generated HTML must start with <!DOCTYPE html>."""
        result = render_plan_html(_FIXTURE_XML)
        assert result.startswith("<!DOCTYPE html>")

    def test_contains_html_root_element(self) -> None:
        """Output must contain opening and closing <html> tags."""
        result = render_plan_html(_FIXTURE_XML)
        assert "<html" in result
        assert "</html>" in result

    def test_contains_head_and_body(self) -> None:
        """Output must have <head> and <body> sections."""
        result = render_plan_html(_FIXTURE_XML)
        assert "<head>" in result
        assert "<body>" in result

    def test_meta_charset_utf8(self) -> None:
        """Document must declare UTF-8 charset."""
        result = render_plan_html(_FIXTURE_XML)
        assert 'charset="UTF-8"' in result

    def test_title_present(self) -> None:
        """A <title> element must be present."""
        result = render_plan_html(_FIXTURE_XML)
        assert "<title>" in result
        assert "</title>" in result


class TestRenderPlanHtmlSelfContained:
    """Verify the generated HTML is fully self-contained (offline-capable)."""

    def test_embeds_plan_xml(self) -> None:
        """The raw SHOWPLAN_XML must be embedded in the HTML."""
        result = render_plan_html(_FIXTURE_XML)
        assert "ShowPlanXML" in result

    def test_no_external_script_src(self) -> None:
        """No <script src="http..."> external load must appear in the output."""
        result = render_plan_html(_FIXTURE_XML)
        # External script loads would have an http(s) URL in a src attribute
        assert not re.search(r'<script[^>]+src=["\']https?://', result)

    def test_no_external_link_href(self) -> None:
        """No <link href="http..."> external stylesheet load must appear."""
        result = render_plan_html(_FIXTURE_XML)
        assert not re.search(r'<link[^>]+href=["\']https?://', result)

    def test_no_qp_icons_png_reference(self) -> None:
        """The bare 'qp_icons.png' filename must not appear — it must be inlined."""
        result = render_plan_html(_FIXTURE_XML)
        assert "url('qp_icons.png')" not in result
        assert 'url("qp_icons.png")' not in result

    def test_css_sprites_inlined_as_data_uri(self) -> None:
        """The qp_icons.png sprite sheet must be base64-inlined as a data URI."""
        result = render_plan_html(_FIXTURE_XML)
        assert "data:image/png;base64," in result

    def test_inline_style_block_present(self) -> None:
        """An inline <style> block must be present (no external stylesheet link)."""
        result = render_plan_html(_FIXTURE_XML)
        assert "<style>" in result

    def test_inline_script_block_present(self) -> None:
        """An inline <script> block must be present (no external JS load)."""
        result = render_plan_html(_FIXTURE_XML)
        assert "<script>" in result

    def _assert_script_breakout_neutralised(self, stmt_suffix: str) -> None:
        """Assert that *stmt_suffix* appended to StatementText does not break out.

        Extracts the content of the plan-XML template literal from the rendered
        HTML and asserts that no literal ``</script`` sequence (case-insensitive)
        appears inside it — which is the exact condition the HTML parser uses to
        close a ``<script>`` element.
        """
        xml_with_payload = _FIXTURE_XML.replace("SELECT 1", stmt_suffix)
        result = render_plan_html(xml_with_payload)
        # Isolate the JS template literal that holds the plan XML.
        start = result.find("var planXml = `")
        end = result.find("`;", start)
        literal_content = result[start:end]
        # No literal </script (case-insensitive) may appear inside the literal.
        assert not re.search(r"</script", literal_content, re.IGNORECASE), (
            f"Literal </script found in template literal for stmt {stmt_suffix!r}"
        )
        # The neutralised form (escaped slash) must be present.
        assert re.search(r"<\\/script", literal_content, re.IGNORECASE), (
            f"Expected <\\/script not found in template literal for stmt {stmt_suffix!r}"
        )
        # The document must still be a complete HTML file.
        assert "</html>" in result

    def test_script_close_tag_lowercase_does_not_break_out(self) -> None:
        """``</script>`` (lowercase) in plan XML must be neutralised."""
        self._assert_script_breakout_neutralised("</script><img src=x onerror=alert(1)>")

    def test_script_close_tag_uppercase_does_not_break_out(self) -> None:
        """``</SCRIPT>`` (uppercase) in plan XML must be neutralised.

        The HTML parser closes a <script> element case-insensitively, so
        ``</SCRIPT>`` is equally dangerous and must be handled.
        """
        self._assert_script_breakout_neutralised("</SCRIPT><img src=x onerror=alert(1)>")

    def test_script_close_tag_mixed_case_does_not_break_out(self) -> None:
        """``</ScRiPt>`` (mixed case) in plan XML must be neutralised."""
        self._assert_script_breakout_neutralised("</ScRiPt><img src=x onerror=alert(1)>")


class TestRenderPlanHtmlJsLibrary:
    """Verify the vendored html-query-plan JS library is correctly wired up."""

    def test_qp_showplan_call_present(self) -> None:
        """QP.showPlan(...) call must appear in the HTML."""
        result = render_plan_html(_FIXTURE_XML)
        assert "QP.showPlan" in result

    def test_container_element_present(self) -> None:
        """A container <div> that QP.showPlan targets must exist."""
        result = render_plan_html(_FIXTURE_XML)
        # The container is referenced by id in the JS call
        assert "id=" in result

    def test_qp_js_not_empty(self) -> None:
        """The vendored qp.min.js content must be non-trivially present."""
        result = render_plan_html(_FIXTURE_XML)
        # The minified JS is over 100 KB — verify at least a few KB are present.
        assert len(result) > 100_000


class TestRenderPlanHtmlXmlEmbedding:
    """Verify that the SHOWPLAN_XML is correctly embedded in the JS literal."""

    def test_xml_content_verbatim_in_output(self) -> None:
        """Key XML content from the input must appear verbatim in the output."""
        result = render_plan_html(_FIXTURE_XML)
        # The namespace URI and element name must survive the embedding.
        assert "ShowPlanXML" in result

    def test_special_chars_in_xml_do_not_break_output(self) -> None:
        """Angle brackets and ampersands in the XML must not break the output."""
        result = render_plan_html(_FIXTURE_XML)
        # The output must still be a complete HTML document
        assert "</html>" in result

    def test_backtick_in_plan_xml_escaped(self) -> None:
        """A backtick character in the plan XML must be escaped in the JS template literal."""
        xml_with_backtick = _FIXTURE_XML.replace("SELECT 1", "SELECT `foo`")
        result = render_plan_html(xml_with_backtick)
        # The backtick must be escaped as \` in the JS template literal
        assert "\\`" in result
        # The output must still be a complete HTML document
        assert "</html>" in result

    def test_backslash_in_plan_xml_escaped(self) -> None:
        """A backslash in the plan XML must be doubled in the JS template literal."""
        xml_with_backslash = _FIXTURE_XML.replace("SELECT 1", "SELECT 'foo\\\\bar'")
        result = render_plan_html(xml_with_backslash)
        assert "</html>" in result


class TestRenderPlanHtmlUnknownOperator:
    """Verify unknown operators degrade gracefully (library handles them)."""

    def test_unknown_operator_does_not_raise(self) -> None:
        """render_plan_html must not raise for an unknown operator type."""
        result = render_plan_html(_UNKNOWN_OP_XML)
        assert isinstance(result, str)
        assert "</html>" in result

    def test_unknown_operator_xml_embedded(self) -> None:
        """Even with unknown operators the XML must be embedded in the output."""
        result = render_plan_html(_UNKNOWN_OP_XML)
        assert "FabricDistributedShuffle" in result


class TestRenderPlanHtmlOutputPath:
    """Verify the HTML can be written to a file."""

    def test_write_to_file_produces_valid_html(self) -> None:
        """render_plan_html output can be written to a .html file and re-read."""
        result = render_plan_html(_FIXTURE_XML)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(result)
            tmp_path = Path(fh.name)
        try:
            content = tmp_path.read_text(encoding="utf-8")
            assert content == result
            assert "<!DOCTYPE html>" in content
        finally:
            tmp_path.unlink(missing_ok=True)

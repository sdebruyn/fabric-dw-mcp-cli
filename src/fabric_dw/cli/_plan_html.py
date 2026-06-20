"""Self-contained HTML renderer for SHOWPLAN_XML execution plans.

Produces a single ``.html`` file that embeds the raw SHOWPLAN_XML together
with the vendored `html-query-plan <https://github.com/JustinPealing/html-query-plan>`_
JavaScript library (Justin Pealing, MIT licence) and its stylesheet.  The
result opens offline in any browser — no CDN or network access is required.

The library is vendored under ``src/fabric_dw/assets/html_query_plan/``
(``css/qp.css``, ``css/qp_icons.png``, ``dist/qp.min.js``, ``LICENSE.txt``).
The sprite-sheet PNG is base64-encoded and inlined into the ``<style>`` block
so that the single output file is fully self-contained.

Public API
----------
- :func:`render_plan_html` — build the self-contained HTML string.

Viewing the output
------------------
- Write to a ``.html`` file (via ``-o plan.html``) and open in any web browser.
- Works offline — no internet connection is needed.
"""

from __future__ import annotations

import base64
import html
import importlib.resources

__all__ = ["render_plan_html"]

# Package path to the vendored html-query-plan assets.
_ASSETS_PKG = "fabric_dw.assets.html_query_plan"


def _load_asset_text(subpath: str) -> str:
    """Read a text asset from the vendored html-query-plan package.

    Args:
        subpath: Relative path within the assets package, using ``/`` as the
            separator (e.g. ``"css/qp.css"``).

    Returns:
        The file contents as a UTF-8 string.
    """
    pkg = importlib.resources.files(_ASSETS_PKG)
    parts = subpath.split("/")
    resource = pkg
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")


def _load_asset_bytes(subpath: str) -> bytes:
    """Read a binary asset from the vendored html-query-plan package.

    Args:
        subpath: Relative path within the assets package, using ``/`` as the
            separator (e.g. ``"css/qp_icons.png"``).

    Returns:
        The file contents as raw bytes.
    """
    pkg = importlib.resources.files(_ASSETS_PKG)
    parts = subpath.split("/")
    resource = pkg
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_bytes()


def _inline_css() -> str:
    """Return the qp.css stylesheet with ``qp_icons.png`` base64-inlined.

    The original CSS references ``qp_icons.png`` via ``url('qp_icons.png')``.
    For a self-contained file we replace that with a ``data:`` URI so the
    sprite sheet is embedded directly in the ``<style>`` block.

    Returns:
        CSS text with the sprite-sheet PNG inlined as a base64 data URI.
    """
    css = _load_asset_text("css/qp.css")
    png_bytes = _load_asset_bytes("css/qp_icons.png")
    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{b64}"
    # Replace all occurrences of the sprite-sheet reference.
    return css.replace("url('qp_icons.png')", f"url('{data_uri}')")


def _inline_js() -> str:
    """Return the minified qp.min.js script text.

    Returns:
        The JS library source as a string.
    """
    return _load_asset_text("dist/qp.min.js")


def render_plan_html(plan_xml: str) -> str:
    """Build a self-contained HTML page that renders *plan_xml* graphically.

    The raw SHOWPLAN_XML is embedded in the page as a ``<script>`` block and
    passed to ``QP.showPlan()`` from the vendored ``html-query-plan`` library.
    Both the JS and CSS (including the sprite-sheet PNG, base64-encoded) are
    inlined so the file works offline without any CDN or network access.

    Args:
        plan_xml: The raw SHOWPLAN_XML string returned by the Fabric SQL engine.

    Returns:
        A complete HTML document as a UTF-8 string.  Write it to a ``.html``
        file and open in any web browser to view the graphical execution plan.
    """
    css = _inline_css()
    js = _inline_js()
    # Embed the raw XML inside a JS template literal (backtick-delimited).
    # Using a template literal avoids the need to escape single and double
    # quotes from the XML, but requires escaping:
    #   \      → \\     (backslash must be doubled first)
    #   `      → \`     (would close the template literal)
    #   ${     → \${    (would start a template expression)
    #   </script → \x3C/script  (prevents the HTML parser from closing the
    #              surrounding <script> block early, which could let injected
    #              content execute — e.g. StatementText containing
    #              "</script><img src=x onerror=...>")
    # Note: html.escape is NOT applied here — the XML goes into a JS string,
    # not directly into HTML text content, so HTML entity encoding would
    # corrupt the XML.  The </script> neutralisation above is the correct fix.
    xml_escaped_for_js = (
        plan_xml.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("${", "\\${")
        .replace("</script", "\\x3C/script")
    )
    title = html.escape("SQL Execution Plan")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
{css}
    </style>
</head>
<body>
    <div id="qp-container"></div>
    <script>
{js}
    </script>
    <script>
        var planXml = `{xml_escaped_for_js}`;
        QP.showPlan(document.getElementById('qp-container'), planXml);
    </script>
</body>
</html>"""

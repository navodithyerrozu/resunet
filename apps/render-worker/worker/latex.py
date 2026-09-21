"""LaTeX escaping and template rendering.

This is the most security-sensitive file in the render worker, and the reason
is worth stating plainly: **LaTeX is a Turing-complete macro language, and we
are about to feed it strings a stranger typed into a web form.**

Three separate concerns, each handled here:

1. *Injection.* Unescaped input can do considerably more than break the layout.
   `\\input{/etc/passwd}` embeds a server file into the user's own PDF.
   `\\write18{curl ...}` executes a shell command if shell-escape is enabled.
   `\\def\\x{\\x}\\x` is an infinite loop. Escaping is therefore applied by
   *default* to every value that reaches a template -- see `_finalize` below --
   rather than by remembering a filter at each of several hundred call sites.
   Compile-time flags in `compile.py` are the second layer.

2. *Unicode.* pdflatex handles a narrow character repertoire. A resume pasted
   out of Word arrives full of curly quotes, en dashes and non-breaking
   spaces, and an emoji or a CJK glyph will abort the compile with an error no
   user can act on. Known characters are transliterated; unknown non-ASCII is
   dropped with a warning rather than being allowed to fail the build.

3. *Determinism.* Identical input must produce a byte-identical PDF so that
   `ResumeVersion.pdf_sha256` is meaningful for deduplication. Templates carry
   no timestamps; `compile.py` pins `SOURCE_DATE_EPOCH`.

The escaping strategy is `str.translate` with a single translation table, not
chained `.replace()` calls. Chained replacement has a classic ordering bug --
replace `\\` with `\\textbackslash{}` first and a later rule rewrites the
braces you just introduced -- and a single-pass table cannot express that bug.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

logger = structlog.get_logger(__name__)

TEMPLATE_DIR: Final[Path] = Path(__file__).parent / "templates"

# --- Escaping ---------------------------------------------------------------

# TeX's ten special characters. Built as a translation table so every
# substitution happens in one pass over the string.
_SPECIAL_CHARS: Final[dict[str, str]] = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "_": r"\_",
    "%": r"\%",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

# Characters that arrive constantly from Word, Google Docs and PDF copy-paste.
# Mapped to ASCII or a LaTeX command rather than being dropped, because they
# carry real meaning: an en dash in "2019-2023" should survive.
_UNICODE_MAP: Final[dict[str, str]] = {
    "‘": "`",        # left single quote
    "’": "'",        # right single quote / apostrophe
    "“": "``",       # left double quote
    "”": "''",       # right double quote
    "–": "--",       # en dash
    "—": "---",      # em dash
    "…": r"\ldots{}",
    " ": "~",        # non-breaking space
    "•": r"\textbullet{}",
    "·": r"\textperiodcentered{}",
    "→": r"$\rightarrow$",
    "×": r"$\times$",
    "°": r"\textdegree{}",
    "≤": r"$\leq$",
    "≥": r"$\geq$",
    "−": "-",        # minus sign
    "®": r"\textregistered{}",
    "™": r"\texttrademark{}",
    "©": r"\textcopyright{}",
    "€": r"\texteuro{}",
    "£": r"\pounds{}",
    "﻿": "",         # BOM
    "​": "",         # zero-width space
    "‎": "",         # LTR mark
    "‏": "",         # RTL mark
}

_TRANSLATION_TABLE: Final[dict[int, str]] = str.maketrans(
    {**_UNICODE_MAP, **_SPECIAL_CHARS}
)

# Latin-1 accented characters compile fine under inputenc/fontenc, so they are
# preserved. Anything outside this range after translation is unrenderable.
_UNRENDERABLE = re.compile(r"[^\x20-\x7e¡-ſ\n\t]")

# Control characters other than tab/newline have no business in a document and
# some abort the compile outright.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class LatexSafe(str):
    """A string that is already valid LaTeX and must not be escaped again.

    Used only for markup this codebase generates -- never for user input. It
    is a distinct type rather than a flag so that passing user text where
    markup is expected is a type error a reviewer can see.
    """

    __slots__ = ()


def escape_latex(value: object) -> str:
    """Escape an arbitrary value for safe inclusion in a LaTeX document.

    Applied automatically to every template variable. Idempotency is *not*
    claimed: escaping twice double-escapes, which is why `LatexSafe` exists.
    """
    if isinstance(value, LatexSafe):
        return str(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"

    text = str(value)
    text = _CONTROL_CHARS.sub("", text)
    text = text.translate(_TRANSLATION_TABLE)

    dropped = _UNRENDERABLE.findall(text)
    if dropped:
        # Warn rather than fail: losing an emoji is a cosmetic defect, while
        # refusing to render someone's resume is an outage for that user.
        logger.warning(
            "latex.unrenderable_characters_dropped",
            count=len(dropped),
            sample=[hex(ord(c)) for c in dropped[:5]],
        )
        text = _UNRENDERABLE.sub("", text)

    return text


def _finalize(value: Any) -> str:  # noqa: ANN401 - Jinja hands us arbitrary objects
    """Jinja `finalize` hook: escape everything on its way into the output.

    This inverts the usual failure mode. With an opt-in `| e` filter, a
    forgotten filter is an injection vector that renders correctly in testing.
    With escape-by-default, a forgotten `LatexSafe` wrapper produces visibly
    broken markup instead -- a bug you find immediately and cannot ship.
    """
    return escape_latex(value)


# --- Template environment ---------------------------------------------------


def build_environment(template_dir: Path | None = None) -> Environment:
    """Construct the Jinja environment for LaTeX templates.

    The delimiters are non-default and must be: LaTeX uses `{` and `%`
    natively, so Jinja's `{{ }}` and `{% %}` would collide with real markup
    and with every comment in the preamble. The chosen forms do not occur in
    valid TeX.

        <<  value  >>      variable
        <%  if x  %>       statement
        <#  note  #>       comment

    `autoescape` stays False because Jinja's HTML escaper is wrong for TeX;
    `finalize` does the equivalent job with the correct rules.
    """
    env = Environment(
        loader=FileSystemLoader(template_dir or TEMPLATE_DIR),
        variable_start_string="<<",
        variable_end_string=">>",
        block_start_string="<%",
        block_end_string="%>",
        comment_start_string="<#",
        comment_end_string="#>",
        autoescape=False,  # noqa: S701 - see docstring; finalize handles escaping
        finalize=_finalize,
        # A missing variable must be an error, not an empty string. Silently
        # omitting a job title produces a plausible-looking resume with a hole
        # in it, which is worse than a failed render.
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["safe_tex"] = LatexSafe
    env.filters["join_safe"] = _join_safe
    return env


def _join_safe(items: list[object], separator: str = ", ") -> LatexSafe:
    """Join a list with each element escaped, then mark the result safe.

    Needed because escaping the joined string would also escape the separator
    when it contains markup (e.g. `\\textbullet{}` between skills).
    """
    return LatexSafe(separator.join(escape_latex(i) for i in items))


def render_template(template_name: str, context: dict[str, Any]) -> str:
    """Render a `.tex.j2` template to LaTeX source."""
    env = build_environment()
    template = env.get_template(template_name)
    return template.render(**context)

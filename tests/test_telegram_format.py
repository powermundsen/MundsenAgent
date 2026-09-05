from __future__ import annotations

import random
import unittest

from mundsen_agent.bridge.telegram_format import (
    chunk_markdown,
    markdown_to_telegram_html,
    render_table_block,
)


class TestTelegramFormatting(unittest.TestCase):
    def test_inline_markdown_and_header_become_html(self) -> None:
        rendered = markdown_to_telegram_html(
            "# Status\n**Ferdig** og *klar* med `kode`."
        )

        self.assertIn("<b>Status</b>", rendered)
        self.assertIn("<b>Ferdig</b>", rendered)
        self.assertIn("<i>klar</i>", rendered)
        self.assertIn("<code>kode</code>", rendered)

    def test_markdown_table_becomes_preformatted_box(self) -> None:
        rendered = markdown_to_telegram_html(
            "| Navn | Status |\n|---|---|\n| Eksempel | Klar |"
        )

        self.assertTrue(rendered.startswith("<pre>"))
        self.assertIn("Eksempel", rendered)
        self.assertIn("┌", rendered)

    def test_table_renderer_handles_uneven_rows(self) -> None:
        rendered = render_table_block(
            ["| A | B |", "|---|---|", "| bare én |"]
        )

        self.assertIn("bare én", rendered)
        self.assertTrue(rendered.endswith("┘"))

    def test_simple_telegram_html_is_preserved(self) -> None:
        rendered = markdown_to_telegram_html("<b>trygt</b>")
        self.assertEqual(rendered, "<b>trygt</b>")

    def test_tags_with_attributes_and_unknown_tags_are_escaped(self) -> None:
        rendered = markdown_to_telegram_html(
            '<a href="https://example.invalid">lenke</a><script>x</script>'
        )

        self.assertIn("&lt;a href=", rendered)
        self.assertIn("&lt;/a&gt;", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)

    def test_existing_entity_and_inline_code_are_not_double_escaped(self) -> None:
        rendered = markdown_to_telegram_html("A &amp; B og `A &amp; B`")

        self.assertEqual(rendered.count("&amp;"), 2)
        self.assertNotIn("&amp;amp;", rendered)

    def test_unknown_named_entity_is_escaped(self) -> None:
        rendered = markdown_to_telegram_html("A &not-a-telegram-entity; B")
        self.assertIn("&amp;not-a-telegram-entity;", rendered)

    def test_fenced_code_is_escaped(self) -> None:
        rendered = markdown_to_telegram_html(
            "```python\nprint('<unsafe>')\n```"
        )

        self.assertEqual(
            rendered, "<pre>print(&#x27;&lt;unsafe&gt;&#x27;)</pre>"
        )

    def test_fenced_code_without_language_keeps_first_word(self) -> None:
        rendered = markdown_to_telegram_html("```\nhello\nworld\n```")
        self.assertEqual(rendered, "<pre>hello\nworld</pre>")

    def test_plain_long_line_is_split_to_rendered_limit(self) -> None:
        source = ("tekst & mer " * 120).strip()
        chunks = chunk_markdown(source, max_html=100)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(
            all(len(markdown_to_telegram_html(chunk)) <= 100 for chunk in chunks)
        )

    def test_large_fenced_code_is_split_without_content_loss(self) -> None:
        source = "```python\n" + ("print('x')\n" * 80) + "```"
        chunks = chunk_markdown(source, max_html=120)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(
            all(len(markdown_to_telegram_html(chunk)) <= 120 for chunk in chunks)
        )
        self.assertEqual(sum(chunk.count("print('x')") for chunk in chunks), 80)

    def test_empty_and_invalid_limits(self) -> None:
        self.assertEqual(chunk_markdown(""), [""])
        with self.assertRaises(ValueError):
            chunk_markdown("tekst", max_html=20)

    def test_chunker_handles_deterministic_special_character_fuzz(self) -> None:
        randomizer = random.Random(42)
        alphabet = "abc XYZ 123 &<>*_`\\n"
        for _ in range(50):
            source = "".join(
                randomizer.choice(alphabet)
                for _ in range(randomizer.randint(1, 500))
            )
            chunks = chunk_markdown(source, max_html=100)
            self.assertEqual("".join(chunks), source)
            self.assertTrue(
                all(
                    len(markdown_to_telegram_html(chunk)) <= 100
                    for chunk in chunks
                )
            )


if __name__ == "__main__":
    unittest.main()

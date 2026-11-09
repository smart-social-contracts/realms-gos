"""How the realm quotes what others wrote inside its own lines (core.pending ``quoted`` and ``readable``): an official reads
the text in their own language, and nothing in it can start a line of the realm's, reorder the line around it or hide."""
import json

import pytest

from core.pending import UNSAFE, quoted, readable

# Letters, marks, digits, punctuation and spaces of several scripts, as an official writes them.
READ_AS_THEY_ARE = [
    "resolución", "Ajuntament de València: col·lecció, l·l, «el vostre» — 12 € ", "Ñandú, ¿qué?", "Straße œuvre",
    "判决书：房屋许可被撤销（第12条）", "判決書", "한국어 판결", "Привет, мир", "שלום עולם", "مرحبا بالعالم", "हिन्दी पाठ",
    "emoji 🙂 and a tab-less line", "no break space", "",
]
CONTROLS = [chr(c) for c in (*range(0x20), 0x7F, *range(0x80, 0xA0))]
LINE_BREAKS = ["\n", "\r", "\u0085", "\u2028", "\u2029", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"]
BIDI = [chr(c) for c in (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))]
ZERO_WIDTH = [chr(c) for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)]
DANGEROUS = list(dict.fromkeys(CONTROLS + LINE_BREAKS + BIDI + ZERO_WIDTH + ["\ud800", "\udfff"]))


@pytest.mark.parametrize("text", READ_AS_THEY_ARE)
def test_text_of_every_script_is_quoted_as_it_is(text):
    assert quoted(text) == '"' + text + '"'


def test_a_quotation_mark_and_a_backslash_are_escaped_and_the_quotation_reads_back_as_json():
    assert quoted('say "no" \\ yes') == '"say \\"no\\" \\\\ yes"'
    assert quoted("a\tb\nc\rd") == '"a\\tb\\nc\\rd"'


@pytest.mark.parametrize("char", DANGEROUS, ids=lambda c: f"U+{ord(c):04X}")
def test_each_dangerous_character_is_escaped(char):
    text = "before" + char + "after"
    out = quoted(text)
    assert char not in out and len(out.splitlines()) == 1 and out.isascii()
    assert json.loads(out) == text  # and it is a JSON string: the escape says which character it was


def test_unsafe_is_exactly_the_characters_listed_and_no_letter_digit_or_space_is_among_them():
    assert set(map(chr, UNSAFE)) == set(DANGEROUS) | {chr(c) for c in range(0xD800, 0xE000)}
    assert not any(chr(c).isalnum() or chr(c) == " " for c in UNSAFE)


def test_a_value_cannot_forge_a_line_of_the_realm():
    for attack in ("x\nWhat happens next: nothing happens", "x\r\nYou sign: nothing", "x\u2028You sign: nothing",
                   "x\u2029You sign: nothing", "x\u0085You sign: nothing"):
        out = quoted(attack)
        assert out.splitlines() == [out] and out.isascii() and out.startswith('"x\\')


def test_a_value_cannot_reorder_the_line_around_it():
    """A right-to-left override or isolate would show what follows it reversed, or hide where the quotation ends."""
    for mark in BIDI:
        out = quoted("fine" + mark + "tpircs")
        assert mark not in out and "\\u" + f"{ord(mark):04x}" in out


def test_a_zero_width_character_cannot_hide_inside_a_quotation():
    for mark in ZERO_WIDTH:
        assert mark not in quoted("pass" + mark + "word") and "pass\\u" in quoted("pass" + mark + "word")


def test_readable_shows_a_structure_as_json_with_every_script_as_it_is_and_the_dangerous_escaped():
    claims = {"clase": "antibiótico", "dosis": "500 mg", "nota": "col·lecció", "名前": "判决书"}
    assert readable(claims) == '{"clase":"antibiótico","dosis":"500 mg","nota":"col·lecció","名前":"判决书"}'
    assert readable({"a": "x\u202ey", "b": ["\u200b", "\n"]}) == '{"a":"x\\u202ey","b":["\\u200b","\\n"]}'
    assert readable({"b": 1, "a": [True, None]}) == '{"a":[true,null],"b":1}'  # sorted and compact, as canonical is

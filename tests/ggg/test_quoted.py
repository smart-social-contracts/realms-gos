"""How the realm quotes what others wrote inside its own lines (core.pending ``quoted`` and ``readable``): an official reads
the text in their own language, and nothing in it can start a line of the realm's, reorder the line around it, or hide
in it: not a control character, a bidi control, a zero-width or tag character, nor a letter that renders as nothing."""
import json

import pytest

from core.pending import FSI, HIDDEN, PDI, SPACES, quoted, readable

# Letters, marks, digits, punctuation and spaces of several scripts, as an official writes them.
READ_AS_THEY_ARE = [
    "resolución", "Ajuntament de València: col·lecció, l·l, «el vostre» — 12 € ", "Ñandú, ¿qué?", "Straße œuvre",
    "判决书：房屋许可被撤销（第12条）", "判決書", "한국어 판결", "Привет, мир", "שלום עולם", "مرحبا بالعالم", "हिन्दी पाठ",
    "حکم صادر شد؛ هزینه ها بر عهده خوانده است", "emoji 🙂 and 👨 on one line", "", "x" * 5000,
    "no\u00a0break\u2003em\u3000ideographic spaces",
]
CONTROLS = [chr(c) for c in (*range(0x20), 0x7F, *range(0x80, 0xA0))]
LINE_BREAKS = ["\n", "\r", "\u0085", "\u2028", "\u2029", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"]
BIDI = [chr(c) for c in (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))]
ZERO_WIDTH = [chr(c) for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)]
INVISIBLE = [chr(c) for c in (
    0x00AD, 0x034F, *range(0x2061, 0x2065), 0x115F, 0x1160, 0x17B4, 0x17B5, *range(0x180B, 0x1810), 0x3164, 0xFFA0,
    *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0))]
# The format characters of the scripts that have them: Arabic number signs, the Syriac abbreviation mark, Egyptian
# hieroglyph format controls, and the Kaithi number signs.
FORMAT = [chr(c) for c in (*range(0x0600, 0x0606), 0x06DD, 0x070F, 0x08E2, 0x110BD, 0x110CD, *range(0x13430, 0x13439))]
TAGS = [chr(c) for c in (0xE0001, 0xE0020, 0xE0041, 0xE007F)]
UNASSIGNED_OR_PRIVATE = [chr(c) for c in (0x0378, 0xE000, 0xF8FF, 0xF0000, 0x10FFFD, 0xFFFF)]
DANGEROUS = list(dict.fromkeys(
    CONTROLS + LINE_BREAKS + BIDI + ZERO_WIDTH + INVISIBLE + FORMAT + TAGS + UNASSIGNED_OR_PRIVATE + ["\ud800", "\udfff"]))


def inside(shown):
    """What a quotation holds: ``shown`` without the isolate it sits in, if it sits in one."""
    return shown[1:-1] if shown.startswith(FSI) and shown.endswith(PDI) else shown


def shows_as_written(text):
    """Whether every character of ``text`` is printable or a space separator, and none renders as nothing."""
    return all((char.isprintable() or char in SPACES) and char not in HIDDEN for char in text)


def isolated(text):
    return FSI + '"' + text + '"' + PDI


@pytest.mark.parametrize("text", READ_AS_THEY_ARE)
def test_text_of_every_script_is_quoted_as_it_is(text):
    assert quoted(text) == isolated(text)


def test_a_quotation_mark_and_a_backslash_are_escaped_and_the_quotation_reads_back_as_json():
    assert quoted('say "no" \\ yes') == isolated('say \\"no\\" \\\\ yes')
    assert quoted("a\tb\nc\rd") == isolated("a\\tb\\nc\\rd")


@pytest.mark.parametrize("char", DANGEROUS, ids=lambda c: f"U+{ord(c):04X}")
def test_each_dangerous_character_is_escaped_and_reads_back_as_itself(char):
    text = "before" + char + "after"
    out = quoted(text)
    assert char not in inside(out) and len(out.splitlines()) == 1 and inside(out).isascii()
    assert inside(out).startswith('"before\\')
    assert json.loads(inside(out)) == text  # a JSON string: the escape says which character it was


def test_a_character_past_ffff_is_written_as_its_surrogate_pair():
    assert quoted("\U000e0041") == isolated("\\udb40\\udc41") and quoted("\U000e0100") == isolated("\\udb40\\udd00")
    assert quoted("\U0010fffd") == isolated("\\udbff\\udffd") and json.loads(inside(quoted("\U000e0041"))) == "\U000e0041"


def test_a_space_separator_stays_as_it_is_and_the_hangul_filler_that_looks_like_one_does_not():
    spaces = "a" + SPACES + "b"
    assert quoted(spaces) == isolated(spaces)
    assert quoted("a\u3164b\uffa0c") == isolated("a\\u3164b\\uffa0c")


def test_every_character_there_is_is_shown_as_written_or_escaped_and_reads_back_as_itself():
    """All of Unicode but the surrogates (a high one followed by a low one would read back as one character)."""
    everything = "".join(chr(c) for c in range(0x110000) if not 0xD800 <= c < 0xE000)
    out = inside(quoted(everything))
    assert shows_as_written(out) and json.loads(out) == everything
    assert shows_as_written(readable({"k": everything})) and json.loads(readable({"k": everything})) == {"k": everything}


def test_a_value_cannot_forge_a_line_of_the_realm():
    for attack in ("x\nWhat happens next: nothing happens", "x\r\nYou sign: nothing", "x\u2028You sign: nothing",
                   "x\u2029You sign: nothing", "x\u0085You sign: nothing"):
        out = quoted(attack)
        assert out.splitlines() == [out] and inside(out).isascii() and inside(out).startswith('"x\\')


def test_a_value_cannot_reorder_the_line_around_it():
    """A right-to-left override or isolate would show what follows it reversed, or hide where the quotation ends."""
    for mark in BIDI:
        out = inside(quoted("fine" + mark + "tpircs"))
        assert mark not in out and "\\u" + f"{ord(mark):04x}" in out


def test_a_quotation_sits_in_an_isolate_that_its_text_cannot_close_or_open():
    """Two Hebrew values side by side keep their own order; a text cannot end its isolate to reach out of it."""
    assert quoted("שלום") == FSI + '"שלום"' + PDI
    out = quoted("a" + PDI + "b" + FSI + "c" + PDI + PDI)
    assert out.startswith(FSI) and out.endswith(PDI) and out.count(FSI) == 1 and out.count(PDI) == 1
    assert quoted("x") + " " + quoted("y") == FSI + '"x"' + PDI + " " + FSI + '"y"' + PDI


def test_a_zero_width_character_cannot_hide_inside_a_quotation():
    for mark in ZERO_WIDTH:
        assert mark not in quoted("pass" + mark + "word") and "pass\\u" in quoted("pass" + mark + "word")


def test_a_clause_written_in_tag_characters_cannot_hide_after_a_visible_text():
    """Tag characters render as nothing: "Refusal annulled" followed by "; fine x1000" in them reads as the first part."""
    hidden = "".join(chr(0xE0000 + ord(c)) for c in "; fine x1000")
    text = "Refusal annulled" + hidden
    for shown in (inside(quoted(text)), readable({"decision": text})):
        assert shows_as_written(shown) and "Refusal annulled" in shown and "\\udb40\\udc3b" in shown  # the ";"
        assert shown.count("\\udb40") == len(hidden)
    assert json.loads(inside(quoted(text))) == text


def test_variation_selectors_soft_hyphens_and_invisible_operators_cannot_hide():
    for text in ("a\u00adb", "a\u2061b\u2062c\u2063d\u2064e", "a\ufe0fb", "a\U000e0101b", "a\u034fb", "a\u180bb"):
        shown = inside(quoted(text))
        assert shows_as_written(shown) and shown != '"' + text + '"' and json.loads(shown) == text


def test_joiners_are_escaped_whatever_script_they_are_in():
    """Two zero-width characters are enough to carry hidden bits, so Persian, Indic and emoji joiners are escaped too."""
    assert quoted("می\u200cخواهم") == isolated("می\\u200cخواهم")
    assert quoted("क्\u200dष क्\u200cष") == isolated("क्\\u200dष क्\\u200cष")
    assert quoted("👨\u200d👩\u200d👧") == isolated("👨\\u200d👩\\u200d👧")
    assert quoted("🏳\ufe0f\u200d🌈") == isolated("🏳\\ufe0f\\u200d🌈")


def test_readable_shows_a_structure_as_json_with_every_script_as_it_is_and_the_dangerous_escaped():
    claims = {"clase": "antibiótico", "dosis": "500 mg", "nota": "col·lecció", "名前": "判决书"}
    assert readable(claims) == '{"clase":"antibiótico","dosis":"500 mg","nota":"col·lecció","名前":"判决书"}'
    assert readable({"a": "x\u202ey", "b": ["\u200b", "\n"]}) == '{"a":"x\\u202ey","b":["\\u200b","\\n"]}'
    assert readable({"b": 1, "a": [True, None]}) == '{"a":[true,null],"b":1}'  # sorted and compact, as canonical is
    assert readable({"a": "x\U000e0041\ufe0f"}) == '{"a":"x\\udb40\\udc41\\ufe0f"}'

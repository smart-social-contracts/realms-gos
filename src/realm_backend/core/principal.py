"""The text form of a principal, checked without the canister's ``Principal``, which gives back whatever text it is
handed: lower-case base32 of the CRC-32 of the principal's bytes followed by the bytes, in groups of five letters
joined by dashes (``aaaaa-aa``, ``2vxsx-fae``). Plain Python, since ``base64``, ``binascii`` and ``zlib`` are stubs or C
modules in a canister."""

ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
INDEX = {letter: i for i, letter in enumerate(ALPHABET)}
CRC_BYTES = 4
MAX_BYTES = 29  # a principal is at most 29 bytes
MAX_TEXT = 63


def crc32(data):
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def text_of(raw):
    """The text form of the principal with bytes ``raw``."""
    data = crc32(raw).to_bytes(CRC_BYTES, "big") + raw
    count = (len(data) * 8 + 4) // 5
    bits = int.from_bytes(data, "big") << (count * 5 - len(data) * 8)
    letters = "".join(ALPHABET[(bits >> 5 * (count - 1 - i)) & 31] for i in range(count))
    return "-".join(letters[i:i + 5] for i in range(0, count, 5))


def valid(text):
    """Whether ``text`` is the text form of a principal: what ``text_of`` gives for the bytes it decodes to, so the right
    checksum, groups and letters, and nothing that merely looks like them."""
    if not isinstance(text, str) or not 0 < len(text) <= MAX_TEXT:
        return False
    letters = text.replace("-", "")
    if not all(letter in INDEX for letter in letters):
        return False
    bits = 0
    for letter in letters:
        bits = bits << 5 | INDEX[letter]
    size, spare = divmod(len(letters) * 5, 8)
    if not CRC_BYTES <= size <= CRC_BYTES + MAX_BYTES:
        return False
    return text_of((bits >> spare).to_bytes(size, "big")[CRC_BYTES:]) == text

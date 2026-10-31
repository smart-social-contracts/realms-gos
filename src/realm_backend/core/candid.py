"""Reading candid text replies without `re` (the canister's has no `compile`), and parsing candid text values."""

ESCAPES = {"n": b"\n", "r": b"\r", "t": b"\t", '"': b'"', "\\": b"\\", "'": b"'"}


def quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unquote(decoded):
    return unquote_bytes(decoded).decode()


def unquote_bytes(decoded):
    body, out, i = decoded[decoded.index('"') + 1:decoded.rindex('"')], bytearray(), 0
    while i < len(body):
        if body[i] != "\\":
            out += body[i].encode()
            i += 1
        elif body[i + 1] in ESCAPES:
            out += ESCAPES[body[i + 1]]
            i += 2
        elif body[i + 1] == "u":
            end = body.index("}", i)
            out += chr(int(body[i + 3:end], 16)).encode()
            i = end + 1
        else:
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
    return bytes(out)


def number(reply, after=""):
    """The first natural number in a candid reply, after ``after`` when given; None if there is none."""
    if after:
        if after not in reply:
            return None
        reply = reply.split(after, 1)[1]
    digits, started = "", False
    for ch in reply:
        if ch.isdigit():
            digits, started = digits + ch, True
        elif started and ch != "_":
            break
    return int(digits) if digits else None


def idl_hash(name):
    """The number Candid stores for a field or case name."""
    h = 0
    for b in name.encode():
        h = (h * 223 + b) % 2**32
    return h


class Principal(str):
    """A principal in a parsed value, told apart from text."""


class Reader:
    """Parses candid text, as candid_decode writes it, into Python data: records and variants become dicts keyed
    by field number (or name, or position), vec a list, opt its value or None, numbers int, blob bytes."""

    def __init__(self, text):
        self.s, self.i = text, 0

    def peek(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1
        return self.s[self.i] if self.i < len(self.s) else ""

    def take(self, ch):
        if self.peek() != ch:
            raise ValueError(f"expected {ch!r} at {self.i} in candid text")
        self.i += 1

    def word(self):
        self.peek()
        start = self.i
        while self.i < len(self.s) and (self.s[self.i].isalnum() or self.s[self.i] in "_-.+"):
            self.i += 1
        return self.s[start:self.i]

    def string(self):
        self.peek()
        start, self.i = self.i, self.i + 1
        while self.s[self.i] != '"':
            self.i += 2 if self.s[self.i] == "\\" else 1
        self.i += 1
        return unquote_bytes(self.s[start:self.i])

    def args(self):
        self.take("(")
        out = []
        while self.peek() != ")":
            out.append(self.value())
            if self.peek() == ",":
                self.i += 1
        self.take(")")
        return out

    def value(self):
        if self.peek() == '"':
            return self.string().decode()
        if self.peek() == "(":  # a parenthesised value, as in ``opt (42 : nat)``
            self.i += 1
            inner = self.value()
            self.take(")")
            return inner
        word = self.word()
        if word in ("record", "variant"):
            return self.fields(word == "variant")
        if word == "vec":
            self.take("{")
            items = []
            while self.peek() != "}":
                items.append(self.value())
                if self.peek() == ";":
                    self.i += 1
            self.take("}")
            return items
        if word == "opt":
            return self.value()
        if word in ("null", "true", "false"):
            return {"null": None, "true": True, "false": False}[word]
        if word == "principal":
            return Principal(self.string().decode())
        if word == "blob":
            return self.string()
        if not word:
            raise ValueError(f"unreadable candid text at {self.i}")
        n = int(word.replace("_", ""))
        if self.peek() == ":":  # a type annotation: ``42 : nat``
            self.i += 1
            self.word()
        return n

    def fields(self, variant=False):
        self.take("{")
        out, position = {}, 0
        while self.peek() != "}":
            mark, label = self.i, self.word()
            if label and self.peek() == "=":
                self.i += 1
                out[int(label.replace("_", "")) if label[0].isdigit() else label] = self.value()
            elif variant:  # a case without a payload: ``variant { Active }``
                out[int(label.replace("_", "")) if label[0].isdigit() else label] = None
            else:  # a tuple's fields, by position
                self.i = mark
                out[position] = self.value()
                position += 1
            if self.peek() == ";":
                self.i += 1
        self.take("}")
        return out

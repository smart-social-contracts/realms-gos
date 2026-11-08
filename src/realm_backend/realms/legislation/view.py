"""The flattened view of one domain as it applies in a realm (layered-codex.md §5.9), as plain data: what
the UI (D8) shows a member and the analysis (C4) compares before and after a change.

Each row is one public member of the domain's resolved class::

    {"name": "ibi_bonus", "kind": "helper", "mark": None,
     "layers": [{"source": "eu/es", "legislation": "TRLRHL Art. 74"},
                {"source": "eu/es/vc/46250-valencia", "legislation": None}]}

``layers`` are the layers that define the name, top to bottom. The last one's definition applies, except
for an ``extension``, where every layer's runs or merges. A setting row also has its resolved
``value`` and, in each layer, the value as it stands after that layer (for an extension, the merge so
far). A forbid's expression is not part of the view: its Cedar, with the realm's settings folded in,
comes with the ``F`` language (S2, #484).
"""

from realms.legislation.context import plain


def flatten(codex, domain):
    """``{"domain", "path", "members"}``: the layers of ``domain`` in this realm and a row for each of
    its public members, in the order the layers declare them."""
    lowest = codex.resolved(domain)  # refuses a domain this realm has no chapter in
    chain = codex.chapters[domain]
    return {
        "domain": domain,
        "path": [cls.__source__ for cls in chain],
        "members": [
            _row(name, member, chain)
            for name, member in lowest.__members__.items()
            if not name.startswith("_")
        ],
    }


def _row(name, member, chain):
    row = {
        "name": name,
        "kind": member.kind,
        "mark": member.mark,
        "layers": [
            _layer(own) for cls in chain for own in [cls.__own__.get(name)] if own
        ],
    }
    if member.kind == "setting":
        row["value"] = plain(member.value)
    return row


def _layer(member):
    layer = {"source": member.source, "legislation": member.legislation}
    if member.kind == "setting":
        layer["value"] = plain(member.value)
    return layer

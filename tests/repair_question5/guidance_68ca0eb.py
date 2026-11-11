def _one_line(text: object) -> str:
    """``text`` with each control character (a line break among them) shown as a space: a name a repository chose can
    never start a line of its own in the reply's guidance. It decides nothing."""
    return "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(text))

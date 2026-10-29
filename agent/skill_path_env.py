"""One-pass metadata path expansion without changing the process environment."""

import ntpath
import posixpath
import re


def expand_metadata_path(entry: str, destination: dict[str, str], *, windows: bool,
                         config_expand=None, env_lookup=None) -> str:
    """Keep native variable/quote syntax; inserted destination values are literal.

    The caller supplies structural worker fields only, never profile secrets.
    Other variables use the supplied profile lookup when present, otherwise the
    host path module. Complete config references can reuse its canonical parser.
    """
    pattern = (r"'[^']*'?|%(%|[^%]*%?)|\$(\$|[-\w]+|\{[^}]*\}?)"
               if windows else r"\$(\w+|\{[^}]*\}?)")
    native = ntpath.expandvars if windows else posixpath.expandvars

    def replace(match):
        token = match[0]
        if config_expand is not None and token.startswith("${") and token.endswith("}"):
            return config_expand(token)
        if token.startswith("'") or token in ("$$", "%%"):
            return native(token)
        name = token[1:]
        if token.startswith("%"):
            if not name.endswith("%"):
                return token
            name = name[:-1]
        elif name.startswith("{"):
            if not name.endswith("}"):
                return token
            name = name[1:-1]
        key = name.upper() if windows else name
        if key in destination:
            return destination[key]
        if env_lookup is not None:
            value = env_lookup(name)
            return token if value is None else value
        return native(token)

    return re.sub(pattern, replace, entry, flags=re.ASCII)

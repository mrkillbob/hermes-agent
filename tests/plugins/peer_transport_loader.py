"""Import ``plugins/peer-transport`` (hyphenated, so not importable by name) as ``peer_transport_plugin``."""
import importlib.util
import sys
from pathlib import Path

NAME = "peer_transport_plugin"


def load():
    if NAME in sys.modules:
        return sys.modules[NAME]
    root = Path(__file__).resolve().parents[2] / "plugins" / "peer-transport"
    spec = importlib.util.spec_from_file_location(
        NAME, root / "__init__.py", submodule_search_locations=[str(root)]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = pkg
    spec.loader.exec_module(pkg)
    return pkg


load()

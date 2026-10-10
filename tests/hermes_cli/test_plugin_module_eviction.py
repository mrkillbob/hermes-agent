"""Plugin eviction tolerates concurrent imports without touching other namespaces."""

import sys
import threading
import types

import pytest

from hermes_cli.plugins_loader import _evict_modules


@pytest.mark.parametrize("remove_existing", [False, True])
def test_module_eviction_tolerates_concurrent_module_changes(monkeypatch, remove_existing):
    package = "hermes_plugins.eviction_race_fixture"
    entered = threading.Event()
    changed = threading.Event()
    imported = "eviction_race_unrelated_import"

    class PauseDuringSelection(str):
        def startswith(self, prefix, *args):
            entered.set()
            assert changed.wait(5), "concurrent module change did not finish"
            return super().startswith(prefix, *args)

    # The child predicate pauses selection so its removal races with eviction,
    # while an unrelated import can grow the registry during selection.
    monkeypatch.setitem(sys.modules, package, types.ModuleType(package))
    monkeypatch.setitem(sys.modules, PauseDuringSelection(package + ".child"),
                        types.ModuleType(package + ".child"))
    sibling = package + "_sibling"
    monkeypatch.setitem(sys.modules, sibling, types.ModuleType(sibling))
    monkeypatch.setitem(sys.modules, imported, types.ModuleType(imported))
    monkeypatch.delitem(sys.modules, imported)

    def concurrent_change():
        if entered.wait(5):
            if remove_existing:
                sys.modules.pop(package + ".child", None)
            else:
                sys.modules[imported] = types.ModuleType(imported)
            changed.set()

    worker = threading.Thread(target=concurrent_change)
    worker.start()
    try:
        _evict_modules(package)
    finally:
        worker.join(5)
        assert not worker.is_alive()
    assert package not in sys.modules
    assert package + ".child" not in sys.modules
    assert sibling in sys.modules
    if not remove_existing:
        assert imported in sys.modules

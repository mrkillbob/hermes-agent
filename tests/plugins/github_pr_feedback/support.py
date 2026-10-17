"""Actual host context with initialized synthetic boards; no service bootstrap."""

from contextlib import closing
from pathlib import Path
import sqlite3

from hermes_cli import kanban_db as kb
from hermes_cli.plugins import PluginContext, PluginManager
from hermes_cli.plugins_manifest import PluginManifest
from hermes_constants import get_hermes_home


def actual_context(home=None):
    home = Path(home if home is not None else get_hermes_home())
    home.mkdir(parents=True, exist_ok=True)
    for slug, path in (("default", home / "kanban.db"),
                       ("tradingbot-burndown", home / "kanban/boards/tradingbot-burndown/kanban.db")):
        path.parent.mkdir(parents=True, exist_ok=True)
        if slug != "default":
            (path.parent / "board.json").write_text('{"slug":"tradingbot-burndown"}', encoding="utf-8")
        if not path.exists():
            with closing(sqlite3.connect(path)) as db:
                db.executescript(kb.SCHEMA_SQL)
                db.execute("ALTER TABLE tasks ADD COLUMN completion_contract TEXT")
                db.commit()
    return PluginContext(PluginManifest(name="github-pr-feedback", source="user"),
                         PluginManager(scope_key=str(home)))

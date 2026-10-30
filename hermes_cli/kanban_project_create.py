"""Opt-in lifecycle guard around ordinary Kanban creation in the calling profile."""
from __future__ import annotations

from hermes_cli import kanban_db, projects_db
from hermes_cli.sqlite_util import write_txn


def create_task(conn, *, project_id=None, board=None, require_active_project=False, **kwargs):
    """Keep project retirement serialized through a committed, confirmed task binding.

    Ordinary callers may link archived projects but must resolve their scope.
    Strict callers supply a canonical ID,
    never a slug or a cross-profile fallback. The registry writer reservation prevents
    archive/delete/rebind commits while the ordinary task transaction commits. Both
    stores remain separate: this is ordering, not a cross-database rollback guarantee.
    Strict callers must supply a connection with no outer transaction so returning
    from ordinary creation means the board commit is durable before reservation release.
    """
    if not require_active_project:
        return kanban_db.create_task(conn, project_id=project_id, board=board, **kwargs)
    if conn.in_transaction:
        raise ValueError('strict creation refuses an active board transaction')
    if not project_id or not projects_db.projects_db_path().is_file():
        raise ValueError('strict creation requires a current project in the calling profile')
    # Initialize via the normal creation path before reserving the writer. Subsequent
    # resolver connections then skip schema initialization and only read this registry.
    with projects_db.connect_closing() as registry, write_txn(registry):
        project = projects_db.get_project(registry, project_id)
        if project is None or project.id != project_id or project.archived:
            raise ValueError('strict creation requires a current canonical project ID')
        destination = kanban_db._normalize_board_slug(board) or kanban_db.get_current_board()
        if project.board_slug and project.board_slug != destination:
            raise ValueError('project board binding changed before task creation')
        task_id = kanban_db.create_task(conn, project_id=project.id, board=board, **kwargs)
        task = kanban_db.get_task(conn, task_id)
        if task is None or task.project_id != project.id:
            # Creation/dedup may already have committed. Do not retry or remove it.
            raise ValueError('created task project binding was not confirmed; inspect the original outcome')
        return task_id

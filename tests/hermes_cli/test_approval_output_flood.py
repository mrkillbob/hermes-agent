"""Approval input remains usable while another thread floods the terminal."""
import asyncio
import contextlib
import threading

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.filters import Condition
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.vt100 import Vt100_Output


class _Terminal:
    encoding = "utf-8"

    def __init__(self, slots):
        self.chunks = []
        self.slots = slots
        self.flood_painted = threading.Event()

    def write(self, text):
        self.chunks.append(text)
        if "background output" in text:
            self.flood_painted.set()
            self.slots.release()

    def flush(self):
        pass

    def isatty(self):
        return True

    def fileno(self):
        return 1


def test_deny_key_during_background_output(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_DEFER_AGENT_STARTUP", "1")
    import cli as cli_module

    app_cli = cli_module.HermesCLI.__new__(cli_module.HermesCLI)
    app_cli._approval_state = None
    app_cli._approval_deadline = 0
    app_cli._approval_lock = threading.Lock()
    app_cli._ring_bell = lambda **_kwargs: None
    app_cli._persist_prompt_summary = lambda *_args: None
    monkeypatch.setattr(cli_module, "_OUTPUT_HISTORY_ENABLED", False)
    kb = KeyBindings()
    kb.add("4", filter=Condition(lambda: bool(app_cli._approval_state)))(
        app_cli._tui_make_approval_number_handler(3))
    layout = Layout(Window(FormattedTextControl(app_cli._get_approval_display_fragments)))
    slots = threading.Semaphore(32)
    terminal = _Terminal(slots)
    output = Vt100_Output(terminal, lambda: Size(rows=40, columns=120),
                          term="xterm-256color", enable_cpr=False)

    async def run():
        with create_pipe_input() as pipe:
            app = Application(layout=layout, key_bindings=kb,
                              full_screen=False, input=pipe, output=output)
            app_cli._app = app
            painted = asyncio.Event()
            approval_painted = asyncio.Event()

            def after_render(_app):
                painted.set()
                if app_cli._approval_state is not None:
                    approval_painted.set()

            app.after_render += after_render
            app_task = asyncio.create_task(app.run_async())
            result = {}
            done = threading.Event()
            flood_active = threading.Event()
            producer = request = None
            try:
                await asyncio.wait_for(painted.wait(), 10)

                def ask():
                    try:
                        result["decision"] = app_cli._approval_callback("synthetic command", "test only")
                        result["flood_was_active"] = flood_active.is_set()
                    finally:
                        done.set()

                def flood():
                    flood_active.set()
                    try:
                        i = 0
                        while not done.is_set():
                            if not slots.acquire(timeout=15):
                                break
                            if done.is_set():
                                slots.release()
                                break
                            cli_module._cprint(f"background output {i}")
                            i += 1
                    finally:
                        flood_active.clear()

                request = threading.Thread(target=ask, daemon=True)
                request.start()
                await asyncio.wait_for(approval_painted.wait(), 10)
                assert "synthetic command" in "".join(terminal.chunks), "approval panel was not visible"
                producer = threading.Thread(target=flood, daemon=True)
                producer.start()
                assert await asyncio.to_thread(terminal.flood_painted.wait, 10), "no background output painted"
                pipe.send_text("4")  # deny; no command is executed
                assert await asyncio.to_thread(done.wait, 15), "approval input stalled during output flood"
                assert result == {"decision": "deny", "flood_was_active": True}
            finally:
                done.set()
                state = app_cli._approval_state
                if state is not None:
                    state["response_queue"].put("deny")
                for _ in range(32):
                    slots.release()
                alive = []
                try:
                    for worker in (producer, request):
                        if worker is not None:
                            await asyncio.to_thread(worker.join, 10)
                            if worker.is_alive():
                                alive.append(worker.name)
                finally:
                    if app.is_running:
                        app.exit()
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.wait_for(app_task, 10)
                assert not alive, f"synthetic workers did not stop: {alive}"

    asyncio.run(run())

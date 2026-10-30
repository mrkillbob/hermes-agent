"""Verify the runner command against the real installed CLI parser."""
from types import SimpleNamespace

from hermes_cli._parser import build_top_level_parser
from hermes_cli.fleet_runner import FleetRunner


def test_fleet_execution_argv_is_accepted_by_current_chat_parser(monkeypatch):
    observed = []

    def execute(argv, input_text):
        observed.append((argv, input_text))
        return SimpleNamespace(returncode=0, stdout='{"type":"result","text":"ok"}', stderr='')

    runner = FleetRunner('fixture-node', None, 'hermes', ['fixture-profile'], [],
                         profile_providers={'fixture-profile': 'fixture-provider'}, executor=execute)
    task = SimpleNamespace(body='Summarize the supplied public fixture: 50% & Unicode π.\nSecond line.',
                           requirement=SimpleNamespace(models=('fixture-model',)))
    runner._execute_task(task, 'fixture-profile')
    argv, input_text = observed[0]
    assert argv[:3] == ['hermes', '-p', 'fixture-profile']
    args = build_top_level_parser()[0].parse_args(argv[3:])
    assert args.command == 'chat'
    assert args.query_file == "-"
    assert args.query is None
    assert input_text == task.body
    assert task.body not in argv
    assert args.output_format == 'stream-json'
    assert (args.model, args.provider) == ('fixture-model', 'fixture-provider')

    import io
    from hermes_cli.main_query_file import _read_query_file
    monkeypatch.setattr('sys.stdin', io.StringIO(input_text))
    _read_query_file(args)
    assert args.query == task.body


def test_default_executor_transmits_unicode_query_over_stdin():
    import json
    import sys

    text = 'Public fixture: 50% & Unicode π.\nSecond line.'
    result = FleetRunner._execute([sys.executable, '-c',
                                  'import json,sys; print(json.dumps(sys.stdin.read()))'], text)
    assert result.returncode == 0
    assert json.loads(result.stdout) == text


def test_default_executor_stops_on_lease_loss_and_retains_output(tmp_path):
    import sys
    import threading
    from tests.waiting import wait_until
    cancel = threading.Event()
    results = []
    worker = threading.Thread(target=lambda: results.append(FleetRunner._execute([sys.executable, '-c',
        'import sys,threading; print("partial",flush=True); print("diagnostic",file=sys.stderr,flush=True); threading.Event().wait(30)'],
        'Public fixture', cancel=cancel, output_dir=tmp_path)))
    worker.start()
    try:
        assert wait_until(lambda: any('partial' in path.read_text(encoding='utf-8-sig')
                          for path in tmp_path.glob('stdout-*')), timeout=10)
    finally:
        cancel.set()
        worker.join(10)
    assert not worker.is_alive()
    result = results[0]
    assert result.returncode != 0
    assert 'partial' in result.stdout
    assert 'diagnostic' in result.stderr
    assert result.transport_interrupted


def test_transport_failure_without_receipt_is_held_not_retried():
    from tests.hermes_cli.test_fleet_runner import _Coordinator
    coordinator = _Coordinator()
    executions = []

    def fail(argv, text):
        executions.append(argv)
        return SimpleNamespace(returncode=124, stdout='', stderr='transport failed')

    runner = FleetRunner('windows', coordinator, 'hermes', ['coding-expert'], ['LunaBot'],
                         capabilities=[coordinator.capability], executor=fail)
    assert runner.run_once(now=100)
    assert not runner.run_once(now=101)
    assert not coordinator.completed
    assert not coordinator.failed
    assert len(coordinator.uncertain) == 1
    assert len(executions) == 1


def test_missing_launcher_reports_failed_claim(tmp_path):
    from tests.hermes_cli.test_fleet_runner import _Coordinator
    coordinator = _Coordinator()
    runner = FleetRunner('windows', coordinator, str(tmp_path / 'missing-hermes.exe'),
                         ['coding-expert'], ['LunaBot'], capabilities=[coordinator.capability])
    assert runner.run_once(now=100)
    assert not coordinator.completed
    assert coordinator.failed[0][:2] == ('global-task-1', 'claim-1')
    assert 'FileNotFoundError' in coordinator.failed[0][2]['error']

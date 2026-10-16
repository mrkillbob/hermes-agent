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

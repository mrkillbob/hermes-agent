"""Verify the runner command against the real installed CLI parser."""
from types import SimpleNamespace

from hermes_cli._parser import build_top_level_parser
from hermes_cli.fleet_runner import FleetRunner


def test_fleet_execution_argv_is_accepted_by_current_chat_parser():
    observed = []

    def execute(argv):
        observed.append(argv)
        return SimpleNamespace(returncode=0, stdout='{"type":"result","text":"ok"}', stderr='')

    runner = FleetRunner('fixture-node', None, 'hermes', ['fixture-profile'], [],
                         profile_providers={'fixture-profile': 'fixture-provider'}, executor=execute)
    task = SimpleNamespace(body='Summarize the supplied public fixture.',
                           requirement=SimpleNamespace(models=('fixture-model',)))
    runner._execute_task(task, 'fixture-profile')
    argv = observed[0]
    assert argv[:3] == ['hermes', '-p', 'fixture-profile']
    args = build_top_level_parser()[0].parse_args(argv[3:])
    assert args.command == 'chat'
    assert args.query == task.body
    assert args.output_format == 'stream-json'
    assert (args.model, args.provider) == ('fixture-model', 'fixture-provider')

"""Pre-interpreter protection and explicit child handoff for managed launch paths."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
import subprocess
import sys

from pm.lifetime import activity, active_references, domain_for_generation, Gate
from pm.lifetime_handles import inherit

_BORROWED = ()
_FLAG = '--pm-lifetime-references='


def adopt_arguments(arguments):
    """Consume private startup handles before public CLI parsing; retain them until exit."""
    global _BORROWED
    retained = []
    for argument in arguments[:]:
        if argument.startswith(_FLAG):
            values = argument[len(_FLAG):].split(',')
            if not values or any(not value.isdecimal() for value in values):
                raise ValueError('invalid lifetime handoff')
            retained.extend(int(value) for value in values)
            arguments.remove(argument)
    _BORROWED = tuple(dict.fromkeys([*_BORROWED, *retained]))


def target_generation(executable):
    """Keep the venv invocation directory even when its executable is an outside symlink."""
    executable = Path(executable).absolute()
    directory = executable.parent.resolve()
    if directory.name.lower() not in {'bin', 'scripts'}:
        return None
    environment = directory.parent
    generation = environment.parent if environment.name == 'venv' else environment
    domain = domain_for_generation(generation)
    known = generation.parent.name == 'test-environment' and generation.name.startswith('gen-')
    if known or (generation / '.lease-managed').is_file() or Gate(domain).marker.exists():
        return generation
    return None  # external/legacy interpreters never acquire collection qualification


@contextmanager
def protected(command, *, managed=False, options=None):
    own_interpreter = (not isinstance(command, (str, bytes)) and _BORROWED
                       and Path(command[0]).absolute() == Path(sys.executable).absolute())
    generation = target_generation(command[0]) if not isinstance(command, (str, bytes)) and not own_interpreter else None
    owner = activity(domain_for_generation(generation), generation=generation) if generation else nullcontext()
    with owner:
        references = (*_BORROWED, *active_references())
        with inherit(references, options) as handoff:
            args = command
            if managed and handoff.references:
                args = [*command, _FLAG + ','.join(map(str, handoff.references))]
            yield args, handoff.options


def popen(command, *, managed=False, **options):
    with protected(command, managed=managed, options=options) as (args, inherited):
        return subprocess.Popen(args, **inherited)


def run(command, *, managed=False, **options):
    options.setdefault("check", False)
    with protected(command, managed=managed, options=options) as (args, inherited):
        return subprocess.run(args, **inherited)  # noqa: PLW1510 -- check is explicitly forwarded in options


def main(arguments=None):
    """A non-removable bootstrap supervises a target; status and signals remain observable."""
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    managed = bool(arguments and arguments[0] == '--managed')
    if managed:
        arguments.pop(0)
    if not arguments:
        raise ValueError('a target interpreter is required')
    # The child inherits its own kernel reference before its first instruction.
    # A hard exit of this supervisor cannot release that child's protection.
    with popen(arguments, managed=managed) as child:
        try:
            code = child.wait()
        except BaseException:
            child.terminate()
            child.wait()
            raise
    return code if code >= 0 else 128 - code


if __name__ == '__main__':
    raise SystemExit(main())

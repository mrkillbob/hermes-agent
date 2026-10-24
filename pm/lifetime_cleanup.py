"""Failure cleanup for outputs exclusively created by a participating builder."""
from pathlib import Path
import shutil
import stat


def output_identity(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('build output is not a concrete directory')
    return info.st_dev, info.st_ino


def defer_failed_output(path, identity):
    """Defer this producer's cleanup until its writer reference has closed."""
    from pm.lifetime import _ACTIVE

    path = Path(path).absolute()
    path = path.parent.resolve() / path.name
    for owner in reversed(_ACTIVE.get()):
        if owner.fd is not None and path != owner.domain and path.is_relative_to(owner.domain):
            owner.failed_outputs[path] = identity
            return True
    return False  # existing external fresh-output ownership/cleanup is unchanged


def finish_failed_outputs(owner, guard):
    """Under the gate, preserve changed outputs and any surviving child/ancestor hold."""
    from pm.lifetime import _identity

    if (_identity(owner.domain) != owner.domain_identity
            or guard._base_snapshot() != owner.coordination):
        return
    # Validate the complete ownership graph before touching any ancestor: a
    # changed nested output must not subsequently be removed through its parent.
    outputs = owner.failed_outputs.items()
    if any(not path.parent.resolve().is_relative_to(owner.domain)
           or output_identity(path) != identity for path, identity in outputs):
        return
    for path, _ in sorted(outputs, key=lambda pair: len(pair[0].parts), reverse=True):
        if not guard.held(generation=path):
            shutil.rmtree(path, ignore_errors=True)

"""Keep test bridges out of the developer's own reply registry.

A bridge that starts registers itself for `alb --reply-to` under
XDG_CONFIG_HOME (else ~/.config). A test module that starts one points that
at a temporary folder for its whole run:

    setUpModule, tearDownModule = registry_isolation.start, registry_isolation.stop
"""
import os
import shutil
import tempfile
from unittest import mock

_environ = mock.patch.dict(os.environ)


def start():
    _environ.start()
    os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="alb-xdg-")


def stop():
    shutil.rmtree(os.environ["XDG_CONFIG_HOME"], ignore_errors=True)
    _environ.stop()

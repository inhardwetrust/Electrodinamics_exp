# vispy_errors.py
#
# VisPy catches an exception raised in an event callback (a mouse handler of
# the view), logs "ERROR: Invoking ..." with the traceback and goes on: a
# check would pass over a crash in a handler. install() collects those log
# records; a check calls it first and fails if any were collected:
#
#   from vispy_errors import install
#   vispy_errors = install()
#   ...
#   check("no errors in VisPy callbacks", not vispy_errors, vispy_errors[:1])

import logging

from vispy.util import logger


class _Collect(logging.Handler):
    def __init__(self, found):
        super().__init__(level=logging.ERROR)
        self.found = found

    def emit(self, record):
        self.found.append(record.getMessage().splitlines()[0])


def install():
    """A list that fills with the first line of every error VisPy logs."""
    found = []
    logger.addHandler(_Collect(found))
    return found

import os
import shutil

import pytest

_TEST_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "test_output")

# NOTE: the ETag-cache redirect and the per-test commit-author memo reset used to
# live here as autouse fixtures. They now live in ``tests/__init__.py``, because
# conftest.py is a pytest-only mechanism and the suite is also run with plain
# ``unittest`` — under which those fixtures never fired and seven tests failed on
# leaked memo state. Anything the whole suite depends on belongs in the package
# __init__, not here.


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_output():
    """Remove the tests/test_output directory after the full test session completes."""
    yield
    if os.path.exists(_TEST_OUTPUT_DIR):
        shutil.rmtree(_TEST_OUTPUT_DIR)

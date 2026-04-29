import os
import shutil
import pytest

_TEST_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "test_output")


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_output():
    """Remove the tests/test_output directory after the full test session completes."""
    yield
    if os.path.exists(_TEST_OUTPUT_DIR):
        shutil.rmtree(_TEST_OUTPUT_DIR)

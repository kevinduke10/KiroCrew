"""Keep custom integration tests under the repository's host-isolation fixtures."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def require_repository_test_configuration(request):
    if request.config.rootpath.resolve() != ROOT:
        pytest.fail(
            "Run from the repository root with: python3 -m pytest -c setup.cfg -n0 custom/tests. "
            "The root configuration supplies the required host-isolation fixtures."
        )

import pytest
import sys

exit_code = pytest.main(["-q", "tests"])
print(f"Pytest exit code: {exit_code}")
sys.exit(exit_code)

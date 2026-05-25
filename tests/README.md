# Running Tests

## Installation

Install test dependencies:

```bash
pip install -r tests/requirements.txt
```

## Running Tests

Run all tests:

```bash
pytest tests/ -v
```

Run specific test file:

```bash
pytest tests/test_config_manager.py -v
pytest tests/test_update_checker.py -v
```

Run with coverage:

```bash
pytest tests/ --cov=src --cov-report=html
```

## Test Structure

- `test_config_manager.py` - Configuration management tests
- `test_update_checker.py` - Update checker and version comparison tests

## Writing Tests

Follow these guidelines:

1. Use descriptive test names: `test_<function>_<scenario>`
2. Add docstrings explaining what is being tested
3. Use pytest fixtures for common setup
4. Mock external dependencies (network, filesystem)
5. Test both success and failure cases

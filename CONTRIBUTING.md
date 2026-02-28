# Contributing

## Development Setup

```bash
git clone https://github.com/your-org/cluster-guardian.git
cd cluster-guardian
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Running Tests

```bash
# Unit tests
pytest tests/ -v --timeout=30

# With coverage
pytest tests/ -v --cov=src --cov-report=term-missing

# Integration tests (requires kind cluster)
kind create cluster --name guardian-test
kubectl apply -f tests/fixtures/k8s/
pytest tests/integration/ -v --timeout=120
```

## Code Style

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Make changes with tests
4. Ensure `ruff check` and `pytest` pass
5. Submit a PR against `main`

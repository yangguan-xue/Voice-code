# Contributing

Thanks for your interest in Voice Code!

## Development Setup

```bash
git clone https://github.com/yangguan-xue/Voice-code.git
cd Voice-code
uv sync
cp .env.example .env
# edit .env with your API key
```

## Code Quality

Run these before submitting a PR:

```bash
ruff check src/
mypy src/
pytest tests/ -v
```

## Pull Requests

- Keep changes focused — one feature/fix per PR
- Write clear commit messages
- Update tests if adding functionality
- Make sure all quality checks pass

## License

By contributing, you agree that your contributions will be licensed under AGPL-3.0.

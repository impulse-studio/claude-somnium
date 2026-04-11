# Python Lint & Best Practices for Somnium

Reference skill for code quality rules applied to this project.
Consult this when writing new code, reviewing PRs, or adding modules.

## Toolchain

| Tool | Role | Config location |
|------|------|-----------------|
| **ruff** | Lint + format (replaces flake8, isort, black, bandit, pyupgrade) | `pyproject.toml [tool.ruff]` |
| **pyright** | Static type checking (strict mode) | `pyproject.toml [tool.pyright]` |
| **vulture** | Dead code detection | `scripts/lint.sh` |
| **pip-audit** | Dependency vulnerability scan | `scripts/lint.sh` |
| **scripts/check_file_sizes.py** | Max 500 lines per .py file | custom |
| **scripts/check_function_sizes.py** | Max 60 lines per function | custom |

## File & Function Size Limits

- **Max 500 lines** per Python file (excluding `__init__.py`, `conftest.py`).
  If a file exceeds this, split by responsibility.
- **Max 60 lines** per function/method. If longer, extract helpers or
  restructure. Target ~30 lines for most functions.
- **Max 7 arguments** per function (`PLR0913`). If more, use a Pydantic
  model or dataclass.
- **Max 50 statements** per function (`PLR0915`).

## Ruff Rule Sets (full)

```
F, E, W, I, N, UP, ANN, B, A, COM, C4, DTZ, ISC, ICN, PIE, PT,
Q, RSE, RET, SLF, SIM, TID, TCH, ARG, ERA, PGH, PL, TRY, FLY,
PERF, FURB, RUF, S, PTH
```

Key groups and what they catch:
- **B** (bugbear): mutable defaults, broad except, assert False
- **UP** (pyupgrade): modernize to 3.11+ syntax (`X | Y`, not `Union`)
- **SIM**: simplifiable patterns (if/else to ternary, unnecessary else)
- **PERF**: unnecessary list() in loops, try/except in hot loops
- **FURB**: Path.read_text() over open/read/close, startswith tuples
- **TCH**: move type-only imports to `TYPE_CHECKING` (CLI startup speed)
- **S** (bandit): hardcoded passwords, shell=True, SQL injection, no timeout
- **ERA**: commented-out code
- **ANN**: type annotation enforcement (public APIs)
- **PTH**: prefer pathlib over os.path

### Ignored rules
- `COM812`, `ISC001`: conflict with ruff formatter
- `TRY003`: too strict (forces custom exception for every ValueError message)
- `E501`: line length handled by formatter

### Per-file ignores
- `tests/**`: `S101` (assert), `S105`/`S106` (test passwords), `ANN`, `ARG`, `PLR2004` (magic numbers)
- `__init__.py`: `F401` (re-exports)

## Type Hints (Python 3.11+)

- Use `from __future__ import annotations` in every file.
- Use `X | Y` not `Union[X, Y]`, `X | None` not `Optional[X]`.
- Use `list[str]` not `List[str]`, `dict[str, Any]` not `Dict[str, Any]`.
- All public functions: annotate parameters and return types.
- Tests: no annotation requirements.
- Heavy imports used only for types → `TYPE_CHECKING` block:
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      import voyageai
  ```

## Pyright Strict Mode

Requires:
- Return type annotations on all functions
- No implicit `Any`
- No untyped function calls
- `reportUnnecessaryTypeIgnoreComment = true`

## Project Structure (CLI package)

```
somnium/
  cli/
    __init__.py      # app = Typer(), add_typer() composition
    init.py          # somnium init
    index.py         # somnium index, reindex
    status.py        # somnium status + statusline helpers
    dream.py         # somnium dream
    search.py        # somnium search
    update.py        # somnium update
    uninstall.py     # somnium uninstall
    memory.py        # somnium memory list/show/edit/rm/move/merge
    config.py        # somnium config get/set/list/path
```

Each file handles one command or logical group. The CLI layer is thin:
parse args, call service functions, format output with rich. No business
logic in CLI files.

## Import Organization

1. `__future__` imports
2. Standard library
3. Third-party packages
4. First-party (`somnium.*`)

Prefer absolute imports. Relative imports OK within same subpackage.
Lazy imports for heavy deps (voyageai, duckdb) in CLI command functions.

## Error Handling

- Custom `AppError` hierarchy for user-facing errors.
- CLI catches `AppError` → prints message + exit code.
- Let `ValueError`/`TypeError` from bugs bubble up with traceback.
- Catch `FileNotFoundError`/`PermissionError` → wrap in domain exception.
- Always use `raise X from original` to preserve chain.
- Never catch `BaseException` or bare `except:`.

## Security

- Never hardcode API keys (S105/S106/S107).
- Use Pydantic `SecretStr` for API keys in config models.
- Parameterized DuckDB queries (never f-string SQL with user input).
- Always set `timeout` on HTTP requests (S113).
- No `shell=True` in subprocess calls (S602).

## Testing

- AAA pattern: Arrange, Act, Assert (separated by blank lines).
- One assertion concept per test.
- `tmp_path` for file I/O, `monkeypatch` for env/cwd isolation.
- Coverage target: 80% minimum (branch coverage enabled).
- Mirror source structure: `tests/test_cli/`, `tests/test_services/`.

## Performance

- Batch Voyage API calls, never one-at-a-time in loops.
- Use DuckDB vectorized ops over row-by-row Python.
- `TYPE_CHECKING` imports to keep CLI startup fast.
- Comprehensions over manual for+append loops (PERF401).

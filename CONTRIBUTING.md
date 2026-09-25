# Contributing to 4top

Start with a reproducible bug or a small adapter fixture. The project intentionally
leaves process ownership, persistence and multiplexing to the user and to the
original CLI; proposals for agent loops, cloud accounts, semantic state guesses,
process supervisors or automatic privilege bypasses are outside this release.

## Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ./packages/session-ls -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

To make the `4top` command itself run this checkout, install it as an editable
tool. Otherwise a previously installed copy keeps running older code, which looks
like a bug in the change you just made.

```sh
uv tool install --force --editable .   # both packages stay live
```

No multiplexer is required. `tests/integration` starts real fake agents as real
processes and asserts their argv, cwd, environment and written session file; the
remote tests use a fake `ssh` on `PATH` instead of a network. Tests never access the
developer's native HOME/store or the real state directory. Unexpected external
pytest plugins can be excluded with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin`.

Use `requirements-dev.lock` to reproduce the tested development dependency set.
The root package and session-ls have independent distribution metadata; do not put
both console scripts into the root wheel or create a circular dependency.

## Required evidence

A PR explains the user-visible change, safety boundary, tests, actual environment,
uncovered cases and rollback. Adapter PRs include synthetic/redacted fixtures and
an exact native CLI version with capabilities split into history/new/resume.
No arbitrary plugin code is loaded from history or configuration.

Acceptance must distinguish unit tests, headless Textual, real-process launch,
remote transport and human usability tests. Do not turn a mock result into an authenticated smoke-test
claim. Performance results name the dataset, machine, versions and measurement.
No recorded workload needs to contain real customer data.

Run and update the [acceptance guide](docs/validation/README.md) before a release.
Export the synthetic UI with `python scripts/export_demo.py`; the result must say
DEMO. It is not a real-CLI demonstration or a performance benchmark.

## Reporting

Use the bug template and redact paths, prompts and credentials. For security issues,
see SECURITY.md. Do not attach native auth files, raw production transcripts, or an
unreviewed screen recording. No automated issue upload is performed by 4top.

Code is MIT-licensed. Preserve attribution to the existing session-ls core. Keep
English and Chinese README commands and limitations in sync.

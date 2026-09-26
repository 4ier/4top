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
git config core.hooksPath .githooks   # once per clone: run the gates before commit
```

`scripts/check_docs.py` fails when the documentation stops describing the code: an
undocumented command or key, a documented command that no longer exists, a missing
configuration option, a broken relative link, or vocabulary from a design that has
been removed. It runs in the pre-commit hook, in the test suite and in CI, so a
stale README blocks the change instead of misleading the next reader.

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

## Release

Publishing is a tag; CI does the rest. PyPI versions are immutable, so a gate runs
before anything is uploaded.

1. Set the version in `pyproject.toml`, and in `packages/session-ls/pyproject.toml`
   when the parser changed, and update the changelog.
2. Check locally: `python scripts/check_release.py v<version>`.
3. Tag and push: `git tag v<version> && git push origin v<version>`.
4. CI runs the suite, gates the release, builds both wheels, installs them into a
   fresh virtual environment, publishes `session-ls` first and `4top` second, and
   opens the GitHub release.

The gate is what stops the release that cannot work: the root wheel requires
`session-ls>=0.2.0`, PyPI only ever held 0.1.0, so publishing the root alone would
have produced a package nobody could install. A version already on PyPI is skipped
rather than treated as an error, so re-running a release is safe;
`python scripts/check_release.py --decide` says what would be uploaded.

Uploads use PyPI trusted publishing, so no token is stored here. Configure one
publisher per project on PyPI, pointing at owner `4ier`, repository `4top` and
workflow `publish.yml`, with these environment names:

| Project | Environment |
| --- | --- |
| `session-ls` | `pypi-session-ls` |
| `4top` | `pypi-4top` |

The environment distinguishes two otherwise identical publishers, and each project
publishes from its own job: one job means one OIDC exchange, so a second project
would upload with the first project's token and be refused. The
manual trigger of that workflow defaults to a dry run, which rehearses the whole
path without uploading.

## Reporting

Use the bug template and redact paths, prompts and credentials. For security issues,
see SECURITY.md. Do not attach native auth files, raw production transcripts, or an
unreviewed screen recording. No automated issue upload is performed by 4top.

Code is MIT-licensed. Preserve attribution to the existing session-ls core. Keep
English and Chinese README commands and limitations in sync.

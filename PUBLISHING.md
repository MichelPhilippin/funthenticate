# Publishing to PyPI

This project publishes from GitHub Actions using PyPI Trusted Publishing. No PyPI API token is needed.

## One-time setup

1. Create or log in to a PyPI account at https://pypi.org.
2. Go to https://pypi.org/manage/account/publishing/.
3. Add a pending trusted publisher with:
   - PyPI project name: `funthenticate`
   - Owner: `michel-philippin`
   - Repository name: `funthenticate`
   - Workflow filename: `python-publish.yml`
   - Environment name: `pypi`
4. In the GitHub repository, create an environment named `pypi`.
5. Add required reviewers to the `pypi` environment before publishing real releases.

## Release flow

1. Update `version` in `pyproject.toml`.
2. Commit and push the change.
3. Create a GitHub release for that commit.
4. When the release is published, `.github/workflows/python-publish.yml` builds the package, runs tests and ruff, validates the distributions with Twine, and publishes to PyPI.

## Local preflight

```powershell
uv run pytest tests -xvs
uv run ruff check src tests
uv run python -m build
uv run twine check dist\*
```

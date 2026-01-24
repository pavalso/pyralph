# Publishing to PyPI

This guide documents the process for publishing pyralph to the Python Package Index (PyPI).

## Prerequisites

Install the required build and upload tools:

```bash
pip install build twine
```

## Building Distributions

Build both wheel and source distributions:

```bash
python -m build
```

This creates two files in the `dist/` directory:
- `pyralph-<version>-py3-none-any.whl` (wheel distribution)
- `pyralph-<version>.tar.gz` (source distribution)

## Validating the Package

Before uploading, verify the package metadata and structure:

```bash
twine check dist/*
```

This checks for common issues like missing metadata, invalid classifiers, or malformed descriptions.

## Credentials

### Option 1: API Token (Recommended)

1. Create an API token at https://pypi.org/manage/account/token/
2. For TestPyPI, create a token at https://test.pypi.org/manage/account/token/
3. Store the token securely (e.g., in a password manager or environment variable)

When prompted for credentials during upload:
- Username: `__token__`
- Password: Your API token (including the `pypi-` prefix)

### Option 2: Username and Password

Use your PyPI account username and password. This method is less secure and may require 2FA.

### Storing Credentials

You can store credentials in `~/.pypirc`:

```ini
[pypi]
username = __token__
password = pypi-<your-token>

[testpypi]
username = __token__
password = pypi-<your-test-token>
```

**Security Note**: Ensure `~/.pypirc` has restricted permissions (`chmod 600 ~/.pypirc` on Unix systems).

## Testing with TestPyPI

Before publishing to the production PyPI, test your package on TestPyPI:

1. Build the package:
   ```bash
   python -m build
   ```

2. Upload to TestPyPI:
   ```bash
   twine upload --repository testpypi dist/*
   ```

3. Test installation from TestPyPI:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ pyralph
   ```

   The `--extra-index-url` allows dependencies to be installed from the real PyPI.

4. Verify the installation:
   ```bash
   ralph --help
   ```

## Publishing to PyPI

Once testing is complete, publish to the production PyPI:

```bash
twine upload dist/*
```

After upload, verify the package is available:

```bash
pip install pyralph
```

## Version Management

### Incrementing the Version

The version is defined in `pyproject.toml`:

```toml
[project]
version = "0.1.0"
```

Follow semantic versioning (SemVer):
- **MAJOR** (1.0.0): Breaking changes
- **MINOR** (0.2.0): New features, backward compatible
- **PATCH** (0.1.1): Bug fixes, backward compatible

### Version Conflict Resolution

If you receive a "version already exists" error:

1. Check the current version on PyPI:
   ```bash
   pip index versions pyralph
   ```

2. Update the version in `pyproject.toml` to a higher version number

3. Clean and rebuild:
   ```bash
   rm -rf dist/ build/ *.egg-info
   python -m build
   ```

4. Upload again:
   ```bash
   twine upload dist/*
   ```

**Note**: PyPI does not allow re-uploading the same version, even if the previous upload was deleted.

## Troubleshooting

### Authentication Errors

**Error**: `403 Forbidden` or `Invalid or non-existent authentication information`

**Solutions**:
- Verify your API token is correct and includes the `pypi-` prefix
- Ensure the token has upload permissions for this project
- Check if 2FA is required and properly configured
- For new projects, ensure your token has "Entire account" scope or create a project-scoped token after the first upload

### Version Already Exists

**Error**: `File already exists` or `HTTPError: 400 Bad Request`

**Solutions**:
- Increment the version number in `pyproject.toml`
- Delete the `dist/` directory and rebuild
- PyPI does not allow overwriting existing versions

### Metadata Issues

**Error**: `InvalidDistribution` or metadata validation failures

**Solutions**:
- Run `twine check dist/*` to identify specific issues
- Verify `pyproject.toml` has all required fields:
  - `name`
  - `version`
  - `description`
  - `readme` (pointing to an existing file)
  - `license`
  - `requires-python`
- Ensure the README file exists and is properly formatted
- Check that classifiers are valid PyPI classifier strings

### Build Failures

**Error**: Build fails with missing files or import errors

**Solutions**:
- Verify `MANIFEST.in` includes all necessary files
- Check `pyproject.toml` `[tool.setuptools]` configuration
- Ensure all Python modules are importable
- Run `python -c "import ralph"` to test imports

### SSL Certificate Errors

**Error**: `SSLError` or certificate verification failed

**Solutions**:
- Update `pip` and `twine` to latest versions
- Check system date/time is correct
- On corporate networks, configure proxy certificates

### Network/Upload Errors

**Error**: `ConnectionError` or timeout during upload

**Solutions**:
- Retry the upload (transient network issues)
- Check PyPI status at https://status.python.org/
- For large packages, consider uploading wheel and sdist separately

## Release Checklist

Before each release:

- [ ] Update version in `pyproject.toml`
- [ ] Update CHANGELOG (if maintained)
- [ ] Run tests: `pytest`
- [ ] Clean build artifacts: `rm -rf dist/ build/ *.egg-info`
- [ ] Build distributions: `python -m build`
- [ ] Validate package: `twine check dist/*`
- [ ] Test on TestPyPI (for major releases)
- [ ] Upload to PyPI: `twine upload dist/*`
- [ ] Verify installation: `pip install pyralph`
- [ ] Create git tag: `git tag v<version>`
- [ ] Push tag: `git push origin v<version>`

## Quick Reference

| Task | Command |
|------|---------|
| Install tools | `pip install build twine` |
| Build | `python -m build` |
| Validate | `twine check dist/*` |
| Upload to TestPyPI | `twine upload --repository testpypi dist/*` |
| Upload to PyPI | `twine upload dist/*` |
| Clean build | `rm -rf dist/ build/ *.egg-info` |

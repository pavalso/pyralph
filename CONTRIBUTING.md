# Contributing to Ralph

Thank you for your interest in contributing to Ralph! This guide will help you set up your development environment and build the documentation locally.

## Building Documentation

Ralph uses [Sphinx](https://www.sphinx-doc.org/) to generate documentation. Follow these instructions to build and preview the documentation locally before submitting changes.

### Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

### Installing Documentation Dependencies

Install the required packages from the docs requirements file:

```bash
pip install -r docs/requirements.txt
```

This installs:
- **Sphinx** (>=5.0,<8.0): Core documentation generator
- **sphinx-rtd-theme** (>=1.0,<3.0): Read the Docs theme for consistent styling

### Building the Documentation

#### Cross-Platform Command (Recommended)

Use `sphinx-build` directly, which works on all platforms (Windows, macOS, Linux):

```bash
sphinx-build -b html docs docs/_build
```

This command:
- `-b html`: Specifies HTML output format
- `docs`: Source directory containing `.rst` files
- `docs/_build`: Output directory for generated HTML

#### Using Make (Linux/macOS)

If you have `make` installed, you can use the Makefile:

```bash
cd docs
make html
```

#### Windows Users Without Make

Windows users who don't have `make` installed should use the `sphinx-build` command directly (shown above). This is the recommended approach as it doesn't require any additional tools.

Alternatively, if you prefer a batch file approach, you can create a simple build script:

```batch
@echo off
sphinx-build -b html docs docs/_build
```

### Previewing the Documentation

After building, open the generated HTML in your browser:

#### Windows

```cmd
start docs\_build\index.html
```

Or navigate to `docs\_build\index.html` in File Explorer and double-click to open.

#### macOS

```bash
open docs/_build/index.html
```

#### Linux

```bash
xdg-open docs/_build/index.html
```

Or open `docs/_build/index.html` directly in your preferred browser.

### Rebuilding After Changes

When making changes to documentation files, rebuild using the same `sphinx-build` command. Sphinx will only rebuild files that have changed.

For a complete rebuild (recommended when changing configuration):

```bash
sphinx-build -E -b html docs docs/_build
```

The `-E` flag forces Sphinx to rebuild all files.

## Troubleshooting

### Import Errors During Build

**Problem**: `ImportError` or `ModuleNotFoundError` when building documentation.

**Cause**: Sphinx autodoc needs to import the project modules to extract docstrings.

**Solutions**:

1. Install the project in development mode:
   ```bash
   pip install -e .
   ```

2. Ensure all project dependencies are installed:
   ```bash
   pip install -r requirements.txt
   ```
   (if a requirements.txt exists, otherwise install from pyproject.toml)

3. If specific modules fail to import, check that their dependencies are available in your environment.

### Missing Dependencies

**Problem**: `sphinx-build: command not found` or similar errors.

**Solutions**:

1. Verify documentation dependencies are installed:
   ```bash
   pip install -r docs/requirements.txt
   ```

2. If pip install succeeded but command is not found, ensure your Python Scripts directory is in your PATH:
   - **Windows**: Usually `%USERPROFILE%\AppData\Local\Programs\Python\Python3X\Scripts`
   - **Linux/macOS**: Usually `~/.local/bin` or your virtual environment's `bin` directory

3. Use the full path to sphinx-build or run as a module:
   ```bash
   python -m sphinx -b html docs docs/_build
   ```

### Theme Not Found

**Problem**: `sphinx_rtd_theme` is not found.

**Solution**: Install the theme:
```bash
pip install sphinx-rtd-theme
```

Or reinstall all docs dependencies:
```bash
pip install -r docs/requirements.txt
```

### Permission Errors on Windows

**Problem**: Access denied or permission errors when building.

**Solutions**:

1. Close any applications that may have the documentation files open (browsers, editors)

2. Run the command prompt as Administrator (if necessary)

3. Check that the output directory isn't read-only:
   ```cmd
   attrib -r docs\_build /s /d
   ```

### Build Warnings

**Problem**: Sphinx shows warnings during build (yellow text).

**Explanation**: Warnings don't prevent the build from completing but indicate potential issues like:
- Missing cross-references
- Malformed reStructuredText
- Undefined substitutions

**Solution**: Review the warnings and fix the underlying issues in your `.rst` files. Common fixes:
- Ensure all cross-references point to existing targets
- Check indentation in code blocks
- Verify directive syntax

### Stale Build Output

**Problem**: Changes don't appear in the built documentation.

**Solutions**:

1. Force a complete rebuild:
   ```bash
   sphinx-build -E -b html docs docs/_build
   ```

2. Clear the build directory and rebuild:
   ```bash
   # Windows
   rmdir /s /q docs\_build
   sphinx-build -b html docs docs/_build

   # Linux/macOS
   rm -rf docs/_build
   sphinx-build -b html docs docs/_build
   ```

### Python Version Compatibility

**Problem**: Sphinx or extensions fail with your Python version.

**Explanation**: The project supports Python 3.8-3.12. Some Sphinx features require specific Python versions:
- Sphinx 7.2+ requires Python 3.9+
- The docs/requirements.txt pins compatible versions

**Solution**: Use Python 3.8+ and the pinned dependency versions in `docs/requirements.txt`.

## Additional Resources

- [Sphinx Documentation](https://www.sphinx-doc.org/)
- [reStructuredText Primer](https://www.sphinx-doc.org/en/master/usage/restructuredtext/basics.html)
- [Read the Docs Theme Documentation](https://sphinx-rtd-theme.readthedocs.io/)

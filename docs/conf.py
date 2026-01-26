# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import logging
import os
import subprocess
import sys

# Add the source directory to the path for autodoc
sys.path.insert(0, os.path.abspath('../src'))


def get_github_repo_url():
    """Derive GitHub repository URL from git remote origin.

    Returns the HTTPS URL for the GitHub repository, or None if it cannot
    be determined.
    """
    try:
        result = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            timeout=10
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Convert SSH URL to HTTPS if needed
            if url.startswith('git@github.com:'):
                url = url.replace('git@github.com:', 'https://github.com/')
            # Remove .git suffix if present
            if url.endswith('.git'):
                url = url[:-4]
            return url
    except (subprocess.SubprocessError, OSError):
        pass
    return None


# -- GitHub Repository Configuration -----------------------------------------
# Derive repository URL from git remote or use explicit configuration
GITHUB_REPO_URL = get_github_repo_url()

if GITHUB_REPO_URL is None:
    logging.warning(
        "Could not determine GitHub repository URL from git remote. "
        "GitHub link will not be displayed in documentation."
    )

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

# Values extracted from pyproject.toml
project = 'pyralph'
version = '0.1.0'
release = '0.1.0'
author = 'Ralph Agent'

copyright = f'2024, {author}'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
]

# -- Napoleon configuration --------------------------------------------------
# Support both Google and NumPy docstring formats
napoleon_google_docstring = True
napoleon_numpy_docstring = True

# -- Autodoc configuration ---------------------------------------------------
# Order members by source order for consistent documentation
autodoc_member_order = 'bysource'

# Handle modules with no docstrings
autodoc_default_options = {
    'undoc-members': True,
    'show-inheritance': True,
}

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

# Custom CSS for Ralph Wiggum theme - "I'm helping!"
html_css_files = [
    'ralph_theme.css',
]

# Theme options for additional customization
html_theme_options = {
    'style_nav_header_background': '#5BA3C9',  # Ralph's shirt blue (deeper shade)
    'navigation_depth': 4,  # Show more depth in navigation
    'collapse_navigation': False,  # Keep navigation expanded
}

# -- GitHub repository link --------------------------------------------------
# Display GitHub link in the documentation header/navigation
# The sphinx_rtd_theme uses html_context to configure the repository link
if GITHUB_REPO_URL:
    html_context = {
        'display_github': True,
        'github_user': GITHUB_REPO_URL.split('/')[-2] if '/' in GITHUB_REPO_URL else '',
        'github_repo': GITHUB_REPO_URL.split('/')[-1] if '/' in GITHUB_REPO_URL else '',
        'github_version': 'master',
        'conf_py_path': '/docs/',
    }

# -- Search configuration ----------------------------------------------------
# Enable English language search with stemming support for partial word matches
html_search_language = 'en'

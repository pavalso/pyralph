# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

# Add the source directory to the path for autodoc
sys.path.insert(0, os.path.abspath('../src'))

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
html_static_path = []

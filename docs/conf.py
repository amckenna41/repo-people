# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
# Make the repo_people package importable for autodoc
sys.path.insert(0, os.path.abspath('..'))

# -- Project information -----------------------------------------------------

project = 'repo-people'
copyright = '2025, AJ McKenna'
author = 'AJ McKenna'
release = '0.1.0'

# -- General configuration ---------------------------------------------------

extensions = [
    'sphinx.ext.autodoc',       # auto-generate docs from docstrings
    'sphinx.ext.autosummary',   # summary tables for modules/classes
    'sphinx.ext.viewcode',      # add [source] links to generated pages
    'sphinx.ext.napoleon',      # support NumPy/Google-style docstrings
    'sphinx.ext.intersphinx',   # link to other projects' docs (e.g. Python)
]

# Napoleon settings for Google/NumPy style docstrings
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

# Autodoc defaults
autodoc_default_options = {
    'members': True,
    'undoc-members': False,
    'show-inheritance': True,
}
autodoc_typehints = 'description'

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# Intersphinx mapping — link to Python standard library docs
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
}

# -- Options for HTML output -------------------------------------------------

html_theme = 'alabaster'

html_theme_options = {
    'description': 'Collect and export full GitHub user profile data for everyone associated with a repository.',
    'github_user': 'amckenna41',
    'github_repo': 'repo-people',
    'github_button': True,
    'github_type': 'star',
    'fixed_sidebar': True,
}

html_static_path = ['_static']

html_sidebars = {
    '**': [
        'about.html',
        'navigation.html',
        'relations.html',
        'searchbox.html',
    ]
}
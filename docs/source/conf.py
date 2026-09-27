"""Sphinx: импорт кода без запуска серверов и воркеров."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
project = "Delay Prediction Platform"
language = "ru"
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.viewcode"]
html_theme = "alabaster"
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {"members": True, "undoc-members": True}
exclude_patterns = []
napoleon_use_ivar = True

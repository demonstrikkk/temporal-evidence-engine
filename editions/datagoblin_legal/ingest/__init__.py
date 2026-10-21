"""Offline corpus builder for the legal edition.

This package is edition code: it may import Python libraries, but it must not
import core modules other than `app.domain.api`. It writes a normalized corpus;
the core's generic importer turns that corpus into a run, pages and a dataset.
"""

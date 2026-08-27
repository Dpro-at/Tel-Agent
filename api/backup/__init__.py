"""Backup and restore — P7.

Split three ways on purpose: `archive` knows the file format and nothing about the
database's meaning, `targets` knows where files go and nothing about their contents,
and `service` is the only module that decides anything. `restore` is deliberately not
here — it lives in `scripts/restore.py`, because a running process must not rewrite
the database it is holding open.
"""

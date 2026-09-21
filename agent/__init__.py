"""Instinct-style personal agent: git-backed markdown memory + proactive loop."""

from . import memory, tasks


def boot():
    memory.init()
    tasks.init_db()

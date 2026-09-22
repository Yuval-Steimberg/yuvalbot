"""A personal agent: git-backed markdown memory, proactive scheduling, real actions."""

from . import memory, tasks, approvals, config, jobs


def boot():
    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    memory.init()
    tasks.init_db()
    approvals.init_db()
    jobs.init_db()

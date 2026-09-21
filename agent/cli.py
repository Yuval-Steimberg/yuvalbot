"""Local REPL:  python -m agent.cli            → chat
                python -m agent.cli tick       → run due follow-ups
                python -m agent.cli consolidate→ run the nightly pass
                python -m agent.cli search "q" → grep memory
"""
import sys, logging
import agent
from agent import brain, consolidate, memory

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")


def main():
    agent.boot()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "chat"
    if cmd == "tick":
        print(brain.tick()); return
    if cmd == "consolidate":
        print(consolidate.run()); return
    if cmd == "search":
        for h in memory.search(" ".join(sys.argv[2:])):
            print(f"{h['score']:>4}  {h['id']:<40} {h['snippet'][:80]!r}")
        return
    if cmd == "stats":
        print(memory.stats()); return
    print("yuval.bot — ctrl-c to quit\n")
    while True:
        try:
            msg = input("› ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if msg:
            print("\n" + brain.run(msg, channel="cli") + "\n")


if __name__ == "__main__":
    main()

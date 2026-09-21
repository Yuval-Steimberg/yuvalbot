"""Local REPL and operator commands.

  python -m agent.cli                 chat
  python -m agent.cli tick            run due follow-ups now
  python -m agent.cli brief           run the daily review now
  python -m agent.cli consolidate     run the nightly memory pass now
  python -m agent.cli approvals       list what is waiting on you
  python -m agent.cli approvals 3 yes approve (or 'no' to deny) #3
  python -m agent.cli search "japan"  grep memory
  python -m agent.cli stats
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
    if cmd == "brief":
        print(brain.daily_briefing()); return
    if cmd == "approvals":
        from agent import approvals
        if len(sys.argv) > 3:
            print(approvals.decide(int(sys.argv[2]), sys.argv[3] in ("yes", "y", "true")))
        else:
            for a in approvals.pending():
                print(f"#{a['id']}  {a['tool']:<22} {a['summary'][:70]}")
        return
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

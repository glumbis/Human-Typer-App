import os
import sys

if os.environ.get("HT_FAULT_LOG"):          # debugging aid (CI launch check): dump Python stacks on a crash
    import faulthandler
    import signal
    _fault = open(os.environ["HT_FAULT_LOG"], "w")
    faulthandler.enable(file=_fault, all_threads=True)
    if hasattr(signal, "SIGTRAP"):         # Apple Silicon: EXC_BREAKPOINT arrives as SIGTRAP
        faulthandler.register(signal.SIGTRAP, file=_fault, all_threads=True, chain=True)

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from humantyper.selftest import run
        i = sys.argv.index("--selftest")
        out = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        sys.exit(run(out))
    from humantyper.app import main
    main()

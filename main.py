import sys

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from humantyper.selftest import run
        i = sys.argv.index("--selftest")
        out = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        sys.exit(run(out))
    from humantyper.app import main
    main()

"""Print the important part of macOS crash reports (.ips JSON or legacy .crash text).
Usage: python tools/crash_summary.py REPORT [REPORT ...]"""
import json
import sys


def summarize_ips(text):
    head, _, body = text.partition("\n")
    try:
        h = json.loads(head)
        b = json.loads(body)
    except ValueError:
        return text[:6000]
    out = [f"app: {h.get('app_name')} {h.get('app_version')}  os: {h.get('os_version')}  bug_type: {h.get('bug_type')}"]
    exc = b.get("exception", {})
    out.append(f"exception: {exc.get('type')} {exc.get('signal')} {exc.get('subtype', '')} {exc.get('codes', '')}")
    term = b.get("termination", {})
    if term:
        out.append(f"termination: {term.get('namespace')} {term.get('indicator')} {term.get('reasons', '')}")
    asi = b.get("asi")
    if asi:
        out.append(f"asi: {json.dumps(asi)[:2000]}")
    imgs = b.get("usedImages", [])
    ft = b.get("faultingThread", 0)
    for ti, th in enumerate(b.get("threads", [])):
        if ti != ft and not th.get("triggered"):
            continue
        out.append(f"-- thread {ti} {th.get('name', '')} {th.get('queue', '')} (crashed) --")
        for i, fr in enumerate(th.get("frames", [])[:40]):
            img = imgs[fr.get("imageIndex", 0)] if fr.get("imageIndex", -1) < len(imgs) else {}
            out.append(f"  {i:2d} {img.get('name', '?'):<34} {fr.get('symbol', '?')}+{fr.get('symbolLocation', 0)}")
    # also list every thread's top frames briefly
    for ti, th in enumerate(b.get("threads", [])):
        fr = [f.get("symbol", "?") for f in th.get("frames", [])[:6]]
        out.append(f"thread {ti} {th.get('name', '')} {th.get('queue', '')}: {' < '.join(fr)}")
    return "\n".join(out)


for p in sys.argv[1:]:
    print("=" * 30, p)
    with open(p, encoding="utf-8", errors="replace") as f:
        t = f.read()
    print(summarize_ips(t) if p.endswith(".ips") else t[:8000])

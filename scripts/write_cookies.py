"""Write the YTDLP_COOKIES secret to cookies.txt in the Netscape format yt-dlp expects.

Pasting a cookies.txt into GitHub's secret box tends to turn its tabs into spaces, and
yt-dlp then rejects every line. Cookie fields never contain whitespace (only the last,
the value, rarely might), so the tabs can be put back safely.
"""
import os
import sys

text = os.environ.get("YT_COOKIES", "").replace("\r", "")
out, cookies = ["# Netscape HTTP Cookie File"], 0
for line in text.splitlines():
    line = line.strip()
    if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
        continue
    fields = line.split("\t") if line.count("\t") >= 6 else line.split()
    if len(fields) < 7:
        continue
    out.append("\t".join(fields[:6] + [" ".join(fields[6:])]))
    cookies += 1
open("cookies.txt", "w").write("\n".join(out) + "\n")
print(f"cookies.txt: {cookies} cookie(s)")
if not cookies:
    # Shape only, never content, so a bad paste can be diagnosed from the public log.
    lines = text.splitlines()
    print(f"secret: {len(text)} chars, {len(lines)} lines, {text.count(chr(9))} tabs, "
          f"mentions youtube.com: {'youtube.com' in text}, "
          f"fields per line: {sorted({len(l.split()) for l in lines if l.strip()})[:10]}")
sys.exit(0 if cookies else 1)

"""Make the demo video from a silent screen recording: AI voice-over, captions at the bottom, waits sped up.

Scene times come from NightWatch itself (the audit log) and from Docker (the moment the fault was injected),
so the voice-over matches what is on the screen. Free tools only: ffmpeg, edge-tts (Microsoft neural voices), Pillow.

  python lab/video/make_video.py --incident "~/Desktop/Screen Recording 2026-10-12 at 16.05.10.mov" \
      [--tour "~/Desktop/Screen Recording 2026-10-12 at 16.12.40.mov"] [--short] [--blur x,y,w,h]

Output: video-out/nightwatch-demo.mp4 (and nightwatch-short.mp4 with --short).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "video-out"
VOICE = "en-US-AndrewNeural"
W = 1920
FPS = 30
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# ---------- what the voice says (full video, and the short cut for X) ----------
INCIDENT_SCENES = [
    ("intro", "It is 3 a.m. An alert fires. Somebody must wake up, find the cause, and fix it safely. "
              "This is NightWatch, an on-call agent you can trust to run without you watching. "
              "It watches a real shop service. While all is healthy, it uses no AI, and it costs nothing.",
     "This is NightWatch: an AI on-call agent that cannot change anything without your approval."),
    ("inject", "Now I break Redis, for real. Watch mode sees three symptoms, confirms them twice, "
               "and opens one incident, not three alerts.",
     "I break Redis, for real. NightWatch opens one incident."),
    ("investigate", "The agent investigates by itself. It has no shell. It uses typed, read-only tools, and a policy "
                    "service checks every call. It follows the method from Brendan Gregg's Systems Performance: "
                    "one hypothesis, one test. Every link in its causal chain needs proof from a tool result.",
     "It investigates with read-only tools. Every claim needs proof from a tool result."),
    ("plan", "The plan arrives in Slack, in short and simple sentences: the problem, the cause, the plan, the risk, "
             "the rollback, and the check. The details show the evidence. I do not just approve. I give an instruction.",
     "The plan arrives in Slack: cause, risk, rollback and the check. I give an instruction."),
    ("instruction", "My instruction cancels the first plan. The agent tests my instruction and makes a new plan. "
                    "A new plan needs a new approval.",
     None),
    ("approve", "I approve. The approval is signed, and bound to this exact action and to the current state of the "
                "target. NightWatch makes this one change, and then it verifies the original symptom.",
     "I approve. NightWatch makes exactly this change, then verifies it."),
    ("resolved", "All checks pass. The incident is resolved, and every step is in a hash-chained audit log.",
     "All checks pass. Resolved. Your cloud, your key. NightWatch."),
]

TOUR_SCENES = [
    "Every run is a trace in neatlogs: model calls, tool calls and policy decisions. On a real AWS account, a trace "
    "showed most of the time in model calls and guessed lookups. After the fixes, the cost went from 20 cents to "
    "5 cents per incident.",
    "We measure it on twelve real faults. Correct fix decisions went from 8 of 12 to 11 of 12, "
    "with zero unauthorized changes in 60 runs.",
    "Entire links every commit to the agent session that wrote it.",
    "With cfo.ai we planned a low price: bring your own key, from 5 dollars a month, break-even in month 7.",
    "It runs in your own cloud, with your own key: one container, a read-only role, and no inbound ports.",
    "NightWatch. On-call you can trust, even while you sleep.",
]

TITLE = ("NightWatch", "An AI on-call agent you can trust to run without you watching")


def sh(*argv: str) -> str:
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"command failed: {' '.join(argv[:6])} ...\n{r.stderr[-1500:]}")
    return r.stdout


def duration(path: Path) -> float:
    return float(sh("ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)).strip())


def recording_start(path: Path) -> float:
    """macOS names a screen recording by its start time. Cross-check with the end time (mtime) minus the length."""
    by_end = path.stat().st_mtime - duration(path)
    m = re.search(r"(\d{4}-\d\d-\d\d) at (\d{1,2})\.(\d\d)\.(\d\d)", path.name)
    if m:
        by_name = datetime.strptime(f"{m[1]} {m[2]}:{m[3]}:{m[4]}", "%Y-%m-%d %H:%M:%S").timestamp()
        if abs(by_name - by_end) < 20:
            return by_name
        print(f"note: file name time and file end time differ by {by_name - by_end:.0f} s; using the end time")
    return by_end


# ---------- scene times from the audit log and Docker ----------

def incident_marks(start: float, end: float) -> dict[str, float]:
    db = sqlite3.connect(ROOT / "runs" / "audit.db")
    row = db.execute("select incident_id, ts from events where kind='incident_opened' and ts between ? and ? order by ts limit 1",
                     (start, end)).fetchone()
    if not row:
        sys.exit("no incident was opened during this recording (check that the recording contains the whole demo)")
    iid, opened = row
    ev = db.execute("select ts, kind, payload from events where incident_id=? order by seq", (iid,)).fetchall()

    def first(kind, after=0.0, pred=lambda p: True):
        return next((t for t, k, p in ev if k == kind and t >= after and pred(json.loads(p))), None)

    out = docker_kill(start, opened)
    marks = {"intro": start, "inject": out if out else max(start, opened - 30), "investigate": opened}
    marks["plan"] = first("slack_posted") or first("action_proposed") or opened + 60
    guidance = first("approver_guidance", marks["plan"])
    approved = first("action_approved", marks["plan"])
    if guidance and (not approved or guidance < approved):
        marks["instruction"] = guidance
        # the new plan is posted right after the next report
        marks["approve"] = first("report", guidance) or approved or guidance + 30
    else:
        marks["approve"] = approved or marks["plan"] + 30
    marks["resolved"] = first("verification", marks["approve"]) or first("incident_status", marks["approve"]) or marks["approve"] + 20
    closed = first("incident_status", marks["resolved"])
    marks["end"] = min(end, (closed or marks["resolved"]) + 12)   # the final Slack message, then stop
    print(f"incident {iid}: " + ", ".join(f"{k} +{v - start:.0f}s" for k, v in marks.items()))
    return marks


def docker_kill(since: float, until: float) -> float | None:
    out = subprocess.run(["docker", "events", "--since", str(int(since) - 5), "--until", str(int(until) + 1),
                          "--filter", "container=nw-redis", "--filter", "event=kill", "--format", "{{.Time}}"],
                         capture_output=True, text=True, timeout=30).stdout.split()
    return float(out[0]) if out else None


# ---------- voice and captions ----------

async def _tts(text: str, path: Path) -> list[tuple[float, float, str]]:
    import edge_tts
    sentences = []
    c = edge_tts.Communicate(text, VOICE, rate="+4%", boundary="SentenceBoundary")
    with path.open("wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "SentenceBoundary":
                sentences.append((ch["offset"] / 1e7, ch["duration"] / 1e7, ch["text"]))
    return sentences


def voice(text: str, name: str) -> tuple[Path, float, list[tuple[float, float, str]]]:
    import hashlib
    key = hashlib.sha256(f"{VOICE}|{text}".encode()).hexdigest()[:12]
    path, meta = OUT / "work" / f"{name}-{key}.mp3", OUT / "work" / f"{name}-{key}.json"
    if path.exists() and meta.exists():                       # same text and voice: reuse
        sentences = [tuple(x) for x in json.loads(meta.read_text())]
    else:
        for attempt in range(4):                              # the free voice service fails now and then
            try:
                sentences = asyncio.run(_tts(text, path))
                # A broken answer is short audio without sentence marks: about 2.5 words per second is normal.
                if not sentences or duration(path) < len(text.split()) / 5:
                    raise RuntimeError("incomplete audio")
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    raise
                print(f"  voice service: {type(e).__name__}; retry in {2 * (attempt + 1)} s")
                time.sleep(2 * (attempt + 1))
        meta.write_text(json.dumps(sentences))
    # Long sentences: show them in parts, timed by length.
    caps = []
    for t0, d, s in sentences:
        parts = textwrap.wrap(s, 64)
        total = sum(len(p) for p in parts)
        t = t0
        for p in parts:
            dd = d * len(p) / total
            caps.append((t, t + dd, p))
            t += dd
    return path, duration(path), caps


def caption_png(text: str, path: Path, width: int) -> None:
    from PIL import Image, ImageDraw, ImageFont
    size = int(width * 0.026)
    font = ImageFont.truetype(FONT, size)
    lines = textwrap.wrap(text, 64)
    pad = size // 2
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lw = max(d.textlength(l, font=font) for l in lines)
    lh = int(size * 1.25)
    img = Image.new("RGBA", (int(lw) + 2 * pad, lh * len(lines) + pad), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=pad // 2, fill=(0, 0, 0, 185))
    for i, l in enumerate(lines):
        dr.text(((img.width - dr.textlength(l, font=font)) / 2, pad // 2 + i * lh), l, font=font, fill=(255, 255, 255, 255))
    img.save(path)


def badge_png(text: str, path: Path, width: int) -> None:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(FONT, int(width * 0.016))
    tw = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(text, font=font)
    pad = int(width * 0.008)
    img = Image.new("RGBA", (int(tw) + 2 * pad, int(width * 0.016 * 1.3) + pad), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=pad, fill=(230, 140, 0, 220))
    dr.text((pad, pad // 2), text, font=font, fill=(0, 0, 0, 255))
    img.save(path)


def title_png(path: Path, width: int, height: int) -> None:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), (12, 14, 22))
    dr = ImageDraw.Draw(img)
    big, small = ImageFont.truetype(FONT, int(width * 0.06)), ImageFont.truetype(FONT, int(width * 0.022))
    for text, font, y in ((TITLE[0], big, 0.40), (TITLE[1], small, 0.55), ("#neatHack", small, 0.63)):
        dr.text(((width - dr.textlength(text, font=font)) / 2, height * y), text, font=font,
                fill=(255, 255, 255) if font is big else (170, 180, 200))
    img.save(path)


# ---------- one scene = one clip with its voice and captions ----------

def render_scene(src: Path, a: float, b: float, text: str, name: str, height: int, blur: str | None,
                 max_speed: float = 12.0) -> Path:
    audio, adur, caps = voice(text, name)
    target = adur + 0.8
    src_dur = max(0.1, b - a)
    speed = min(max_speed, max(1.0, src_dur / target))
    shown = src_dur / speed
    freeze = max(0.0, target - shown)
    total = shown + freeze
    vf = f"setpts=(PTS-STARTPTS)/{speed:.4f},scale={W}:{height},fps={FPS}"
    if blur:   # after scaling, so the box is in output coordinates
        x, y, w, h = (int(v) for v in blur.split(","))
        vf += f",split[m][c];[c]crop={w}:{h}:{x}:{y},gblur=sigma=14[bl];[m][bl]overlay={x}:{y}"
    if freeze > 0:
        vf += f",tpad=stop_mode=clone:stop_duration={freeze:.3f}"
    inputs = ["-ss", f"{a:.3f}", "-t", f"{src_dur:.3f}", "-i", str(src), "-i", str(audio)]
    graph = f"[0:v]{vf}[v0]"
    last = "v0"
    if speed >= 1.5:   # say that waits are sped up
        png = OUT / "work" / f"{name}-speed.png"
        badge_png(f"{speed:.0f}x faster", png, W)
        inputs += ["-i", str(png)]
        idx = sum(1 for x in inputs if x == "-i") - 1
        graph += f";[{last}][{idx}:v]overlay=W-w-{int(W * 0.015)}:{int(W * 0.015)}:enable='lt(t,{shown:.2f})'[vs]"
        last = "vs"
    for i, (c0, c1, txt) in enumerate(caps):
        png = OUT / "work" / f"{name}-cap{i}.png"
        caption_png(txt, png, W)
        inputs += ["-i", str(png)]
        idx = sum(1 for x in inputs if x == "-i") - 1
        graph += f";[{last}][{idx}:v]overlay=(W-w)/2:H-h-{int(height * 0.05)}:enable='between(t,{c0:.2f},{c1:.2f})'[v{i + 1}]"
        last = f"v{i + 1}"
    out = OUT / "work" / f"{name}.mp4"
    sh("ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", f"[{last}]", "-map", "1:a",
       "-af", "apad", "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out))
    print(f"  {name}: source {src_dur:.0f}s at {speed:.1f}x -> {total:.1f}s (voice {adur:.1f}s)")
    return out


def title_clip(height: int, seconds: float = 3.0) -> Path:
    png, out = OUT / "work" / "title.png", OUT / "work" / "title.mp4"
    title_png(png, W, height)
    sh("ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(seconds), "-i", str(png), "-f", "lavfi", "-t", str(seconds),
       "-i", "anullsrc=r=48000:cl=stereo", "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
       "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(out))
    return out


def concat(parts: list[Path], out: Path) -> None:
    lst = OUT / "work" / f"{out.stem}.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out))
    print(f"wrote {out} ({duration(out):.0f} s)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--incident", required=True, help="screen recording of the incident (watch mode, fault, Slack)")
    ap.add_argument("--tour", help="optional second recording: neatlogs, results, Entire, cfo.ai, self-host guide (in this order)")
    ap.add_argument("--short", action="store_true", help="also make a short cut for X (under 2 min 20 s)")
    ap.add_argument("--blur", help="x,y,w,h in the output size (1920 wide) to blur, for example a workspace name")
    a = ap.parse_args()
    (OUT / "work").mkdir(parents=True, exist_ok=True)

    src = Path(a.incident).expanduser()
    vw, vh = (int(x) for x in sh("ffprobe", "-v", "quiet", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                 "-of", "csv=p=0", str(src)).strip().split(","))
    height = int(W * vh / vw) // 2 * 2
    start = recording_start(src)
    end = start + duration(src)
    marks = incident_marks(start, end)
    names = [n for n, *_ in INCIDENT_SCENES if n in marks]
    bounds = [(n, marks[n] - start, (marks[names[i + 1]] if i + 1 < len(names) else marks["end"]) - start) for i, n in enumerate(names)]
    texts = {n: (full, short) for n, full, short in INCIDENT_SCENES}

    print("full video:")
    parts = [title_clip(height)]
    for n, s0, s1 in bounds:
        parts.append(render_scene(src, max(0.0, s0), s1, texts[n][0], n, height, a.blur))
    if a.tour:
        tour = Path(a.tour).expanduser()
        td = duration(tour)
        # Split the tour by the length of each voice part: record it in the same order, at a calm, even pace.
        lens = [voice(text, f"tour{i + 1}")[1] for i, text in enumerate(TOUR_SCENES)]
        edges = [td * sum(lens[:i]) / sum(lens) for i in range(len(lens) + 1)]
        for i, text in enumerate(TOUR_SCENES):
            parts.append(render_scene(tour, edges[i], edges[i + 1], text, f"tour{i + 1}", height, a.blur, max_speed=3.0))
    concat(parts, OUT / "nightwatch-demo.mp4")

    if a.short:
        print("short cut:")
        sp = [title_clip(height, 2.0)]
        for n, s0, s1 in bounds:
            if texts[n][1]:
                sp.append(render_scene(src, max(0.0, s0), s1, texts[n][1], f"short-{n}", height, a.blur, max_speed=20.0))
        concat(sp, OUT / "nightwatch-short.mp4")
        if duration(OUT / "nightwatch-short.mp4") > 139:
            print("warning: the short cut is longer than 2 min 20 s (the X limit for free accounts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

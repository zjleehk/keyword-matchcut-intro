#!/usr/bin/env python3
"""keyword-matchcut-intro renderer (offline).

Builds a center-locked red keyword match-cut intro from REAL screenshots listed
in a manifest (each with keyword_bbox). Pillow + numpy + ffmpeg. No network.

Example:
  python scripts/render.py --keyword "电子文盲" --manifest work/manifest.json --out out/intro.mp4
"""
import argparse, json, math, os, random, shutil, subprocess, sys, wave
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

RED = (214, 18, 28)
WHITE = (255, 255, 255)

# ---------------------------------------------------------------- fonts
FONT_CANDIDATES = [
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    # Windows
    "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msjhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/seguisym.ttf",
]


def _cmap(path):
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(path, fontNumber=0, lazy=True)
        return set(f.getBestCmap() or {})
    except Exception:
        return None


def _fc_list():
    if not shutil.which("fc-list"):
        return []
    try:
        out = subprocess.run(["fc-list", "--format", "%{file}\n"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    files = [l.strip() for l in out.splitlines() if l.strip().lower().endswith((".ttf", ".otf", ".ttc"))]
    files.sort(key=lambda p: (0 if "bold" in p.lower() else 1, 0 if "cjk" in p.lower() else 1))
    return files


def pick_fonts(text, user_font=None):
    """Return list of (path, cmap) fonts; together they must cover every non-space char."""
    need = {ord(c) for c in text if not c.isspace()}
    cands = ([user_font] if user_font else []) + FONT_CANDIDATES + _fc_list()
    chosen, covered, seen = [], set(), set()
    for p in cands:
        if not p or p in seen or not os.path.exists(p):
            continue
        seen.add(p)
        cm = _cmap(p)
        if cm is None:
            continue
        gain = (need - covered) & cm
        if gain or (user_font and p == user_font):
            chosen.append((p, cm)); covered |= gain
        if need <= covered:
            break
    missing = need - covered
    if missing:
        chars = "".join(chr(c) for c in sorted(missing))
        sys.exit(f"ERROR: no installed font covers: {chars!r}. Install a CJK/Unicode font "
                 f"(e.g. Noto Sans CJK / Source Han Sans / Microsoft YaHei / PingFang) or pass --font. "
                 f"Refusing to render tofu boxes.")
    return chosen


def render_keyword(text, fonts, size):
    """Render text with per-char font fallback on a shared baseline -> RGBA image (tight)."""
    fts = [(ImageFont.truetype(p, size), cm) for p, cm in fonts]
    runs = []
    for ch in text:
        f = fts[0][0]
        if not ch.isspace():
            for ft, cm in fts:
                if ord(ch) in cm:
                    f = ft; break
        if runs and runs[-1][1] is f:
            runs[-1][0] += ch
        else:
            runs.append([ch, f])
    asc = max(f.getmetrics()[0] for _, f in runs)
    desc = max(f.getmetrics()[1] for _, f in runs)
    width = int(sum(f.getlength(t) for t, f in runs)) + size
    img = Image.new("L", (width, asc + desc + size // 2), 0)
    d = ImageDraw.Draw(img)
    x = size // 4
    for t, f in runs:
        d.text((x, asc + size // 8), t, font=f, fill=255, anchor="ls")
        x += f.getlength(t)
    bb = img.getbbox()
    if not bb:
        sys.exit("ERROR: keyword rendered empty")
    return img.crop(bb)


def fit_keyword(text, fonts, W, H):
    """Largest size with height<=0.15H and width<=0.72W (long Latin words shrink)."""
    size = int(H * 0.16)
    while size > 12:
        m = render_keyword(text, fonts, size)
        if m.height <= H * 0.15 and m.width <= W * 0.72:
            return m
        size = int(size * 0.93)
    return render_keyword(text, fonts, 12)

# ---------------------------------------------------------------- manifest

def load_manifest(path, keyword):
    man = json.loads(Path(path).read_text(encoding="utf-8"))
    base = Path(path).resolve().parent
    srcs = []
    for i, s in enumerate(man.get("sources", [])):
        bb = s.get("keyword_bbox")
        if not bb or len(bb) != 4 or bb[2] <= 2 or bb[3] <= 2:
            sys.exit(f"ERROR: source #{i} ({s.get('id')}) has no valid keyword_bbox [x,y,w,h] in image pixels. "
                     f"Measure it (DOM Range / OCR / manual) - the renderer will not guess.")
        img_p = Path(s["image"]);
        img_p = img_p if img_p.is_absolute() else base / img_p
        if not img_p.exists():
            sys.exit(f"ERROR: image not found: {img_p}")
        im = Image.open(img_p).convert("RGB")
        x, y, w, h = [float(v) for v in bb]
        if x < 0 or y < 0 or x + w > im.width + 1 or y + h > im.height + 1:
            sys.exit(f"ERROR: keyword_bbox outside image for {s.get('id')}")
        srcs.append(dict(s, _img=im, _bb=(x, y, w, h)))
    if len(srcs) < 3:
        sys.exit("ERROR: need >=3 screenshot entries (distinct passages).")
    urls = {s.get("url") or s.get("id") for s in srcs}
    if len(urls) < 2:
        print("WARNING: all passages come from one source; >=2 sources recommended.", file=sys.stderr)
    mk = man.get("keyword")
    if mk and mk != keyword:
        print(f"WARNING: manifest keyword {mk!r} != --keyword {keyword!r}", file=sys.stderr)
    return man, srcs

# ---------------------------------------------------------------- frames

def page_bg(im):
    a = np.asarray(im).reshape(-1, 3)
    return tuple(int(v) for v in np.median(a[:: max(1, len(a) // 5000)], axis=0))


def build_shot(src, W, H, kw_w, kw_h, rng, bg_scale):
    """Scale page so keyword bbox is centred & sized ~ red keyword, then mask the original word."""
    x, y, w, h = src["_bb"]
    s = (kw_h / h) * bg_scale
    s = min(s, (kw_w / w) * 1.15 * bg_scale) if w * s > kw_w * 1.4 else s
    im = src["_img"]
    big = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    bgc = page_bg(im)
    canvas = Image.new("RGB", (W, H), bgc)
    cx, cy = (x + w / 2) * s, (y + h / 2) * s
    canvas.paste(big, (int(round(W / 2 - cx)), int(round(H / 2 - cy))))
    d = ImageDraw.Draw(canvas)
    # mask: union of scaled original box and red-keyword box, padded (removes quotes/residue)
    ow, oh = w * s, h * s
    mw = max(ow, kw_w) / 2 + 0.22 * kw_h
    mh = max(oh, kw_h) / 2 + 0.08 * kw_h
    d.rectangle([W / 2 - mw, H / 2 - mh, W / 2 + mw, H / 2 + mh], fill=bgc)
    return canvas


def edge_mask(W, H, inner):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    return np.clip((r - inner) / (1.25 - inner), 0, 1)[..., None]


def dof(img_np, blurred_np, mask):
    return img_np * (1 - mask) + blurred_np * mask

# ---------------------------------------------------------------- audio

def synth_audio(n_shots_frames, fps, duration, sr=48000, seed=0):
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    out = np.zeros(n, np.float32)
    for f in n_shots_frames:  # page flip: band-passed noise burst with fast decay
        t0 = int(f / fps * sr); L = int(0.07 * sr)
        if t0 + L > n:
            continue
        noise = rng.standard_normal(L).astype(np.float32)
        noise = np.convolve(noise, np.ones(6) / 6, "same") - np.convolve(noise, np.ones(40) / 40, "same")
        env = np.exp(-np.linspace(0, 9, L)) * (1 - np.exp(-np.linspace(0, 60, L)))
        out[t0:t0 + L] += noise * env * rng.uniform(0.5, 0.8)
    # focus whoosh 2.4-3.0s
    a, b = int(2.4 * sr), min(n, int(3.0 * sr))
    if b > a:
        L = b - a; noise = rng.standard_normal(L).astype(np.float32)
        noise = np.convolve(noise, np.ones(25) / 25, "same")
        out[a:b] += noise * np.sin(np.linspace(0, np.pi, L)) * 0.9
    # end tone 3.5s: soft sine "ding"
    c = int(min(3.5, duration - 0.3) * sr); L = n - c
    t = np.arange(L) / sr
    out[c:] += 0.25 * np.sin(2 * np.pi * 880 * t) * np.exp(-t * 5) * (1 - np.exp(-t * 200))
    peak = float(np.max(np.abs(out))) or 1.0
    out *= (10 ** (-3 / 20)) / peak  # peak -3 dBFS, no clipping
    return out, sr

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keyword", required=True, help="exact keyword text, kept as-is (no simp/trad conversion)")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True, help="output .mp4")
    ap.add_argument("--duration", type=float, default=4.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--frames-per-shot", type=int, default=3)
    ap.add_argument("--blur", choices=["dof", "none"], default="dof",
                    help="dof = smooth edge depth-of-field (radial/spin blur NOT implemented)")
    ap.add_argument("--font", help="path to a font file to try first")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--qa-dir", help="dir for qa.json / contact_sheet.jpg / ffprobe.json (default: <out>_qa)")
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ERROR: ffmpeg not found in PATH")
    kw = a.keyword
    W, H, fps = a.width, a.height, a.fps
    N = int(round(a.duration * fps))
    sc = a.duration / 4.0  # timeline scales with duration
    t_cut, t_focus, t_fade = 2.4 * sc, 3.0 * sc, 3.5 * sc
    f_cut, f_focus, f_fade = int(t_cut * fps), int(t_focus * fps), int(t_fade * fps)

    man, srcs = load_manifest(a.manifest, kw)
    fonts = pick_fonts(kw, a.font)
    kwmask = fit_keyword(kw, fonts, W, H)
    kw_w, kw_h = kwmask.size
    kx, ky = (W - kw_w) // 2, (H - kw_h) // 2
    red_layer = Image.new("RGB", (W, H), RED)
    full_mask = Image.new("L", (W, H), 0); full_mask.paste(kwmask, (kx, ky))

    rng = random.Random(a.seed)
    # non-periodic source order: shuffled bag refills, never same source twice in a row
    n_shots = math.ceil(f_cut / a.frames_per_shot)
    order, prev = [], None
    while len(order) < n_shots:
        bag = list(range(len(srcs))); rng.shuffle(bag)
        for i in bag:
            if i != prev and len(order) < n_shots:
                order.append(i); prev = i
    shots = [build_shot(srcs[i], W, H, kw_w, kw_h, rng, rng.uniform(0.92, 1.12)) for i in order]

    m_cut = edge_mask(W, H, 0.55)
    qa_dir = Path(a.qa_dir or (str(Path(a.out).with_suffix("")) + "_qa")); qa_dir.mkdir(parents=True, exist_ok=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)

    audio, sr = synth_audio([k * a.frames_per_shot for k in range(n_shots)], fps, a.duration, seed=a.seed)
    wav = qa_dir / "_audio.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", a.out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    kmask_np = np.asarray(full_mask) >= 254
    per_frame, samples, cache = [], [], {}
    last = shots[-1]
    last_np = np.asarray(last, np.float32)
    last_blur = np.asarray(last.filter(ImageFilter.GaussianBlur(H / 90)), np.float32)
    for f in range(N):
        if f < f_cut:
            k = min(f // a.frames_per_shot, n_shots - 1)
            if k not in cache:
                cache.clear()
                s_np = np.asarray(shots[k], np.float32)
                if a.blur == "dof":
                    bl = np.asarray(shots[k].filter(ImageFilter.GaussianBlur(H / 360)), np.float32)
                    s_np = dof(s_np, bl, m_cut)
                cache[k] = s_np
            bg = cache[k]; phase = "cut"
        elif f < f_focus:
            p = (f - f_cut) / max(1, f_focus - f_cut)
            p = p * p * (3 - 2 * p)
            m = edge_mask(W, H, 0.55 - 0.4 * p) if a.blur == "dof" else 0
            bg = dof(last_np, last_blur, m * (0.35 + 0.65 * p)) if a.blur == "dof" else last_np
            phase = "focus"
        elif f < f_fade:
            p = (f - f_focus) / max(1, f_fade - f_focus)
            bg = last_blur * (1 - p) + 255 * p; phase = "fade"
        else:
            bg = np.full((H, W, 3), 255, np.float32); phase = "white"
        frame = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
        frame.paste(red_layer, (0, 0), full_mask)  # keyword: identical position every frame
        arr = np.asarray(frame)
        ff.stdin.write(arr.tobytes())
        red_ok = float(np.mean(np.all(np.abs(arr[kmask_np].astype(int) - RED) < 8, axis=1)))
        luma = float(np.mean(arr[..., 0] * .299 + arr[..., 1] * .587 + arr[..., 2] * .114))
        per_frame.append(dict(frame=f, phase=phase, keyword_red_coverage=round(red_ok, 4), mean_luma=round(luma, 2),
                              source=srcs[order[min(f // a.frames_per_shot, n_shots - 1)]].get("id") if phase == "cut" else None))
        if f % max(1, fps // 10) == 0:
            samples.append((f, frame.resize((W // 6, H // 6))))
    ff.stdin.close(); rc = ff.wait()
    if rc:
        sys.exit("ERROR: ffmpeg failed")
    wav.unlink(missing_ok=True)

    # contact sheet (every 0.1 s)
    cols = 8; tw, th = samples[0][1].size; rows = math.ceil(len(samples) / cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + 18)), (40, 40, 40)); d = ImageDraw.Draw(sheet)
    for i, (f, im) in enumerate(samples):
        x, y = (i % cols) * tw, (i // cols) * (th + 18)
        sheet.paste(im, (x, y)); d.text((x + 4, y + th + 2), f"{f / fps:.1f}s", fill=(255, 255, 255))
    sheet.save(qa_dir / "contact_sheet.jpg", quality=85)

    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", a.out],
                           capture_output=True, text=True).stdout
    (qa_dir / "ffprobe.json").write_text(probe, encoding="utf-8")
    pj = json.loads(probe)

    # QA checks
    step = max(1, fps // 10)
    cut_srcs = [p["source"] for p in per_frame if p["phase"] == "cut"]
    tenth = [per_frame[i]["source"] for i in range(0, f_cut, step)]
    changes = sum(1 for i in range(1, len(tenth)) if tenth[i] != tenth[i - 1])
    sec_srcs = [per_frame[i]["source"] for i in range(0, f_cut, fps)]
    cut_luma = [p["mean_luma"] for p in per_frame if p["phase"] == "cut"]
    luma_jumps = [abs(cut_luma[i] - cut_luma[i - 1]) for i in range(1, len(cut_luma))]
    end = per_frame[-1]
    last_arr = np.asarray(Image.fromarray(arr))
    non_kw = last_arr[np.asarray(full_mask) == 0]
    end_white = float(np.mean(np.all(non_kw > 245, axis=1)))
    vs = next(s for s in pj["streams"] if s["codec_type"] == "video")
    peak_db = 20 * math.log10(float(np.max(np.abs(audio))) + 1e-9)
    rms_db = 20 * math.log10(float(np.sqrt(np.mean(audio ** 2))) + 1e-9)
    checks = {
        "keyword_bbox_fixed_every_frame": min(p["keyword_red_coverage"] for p in per_frame) >= 0.97,
        "background_changes_each_0.1s": changes >= len(tenth) - 2,
        "integer_second_samples_not_all_same_source": len(set(sec_srcs)) > 1 or len(sec_srcs) <= 1,
        "no_fullscreen_flash (max luma jump<25)": max(luma_jumps or [0]) < 25,
        "end_frame_white_bg": end_white > 0.995,
        "audio_peak_below_-1dBFS": peak_db < -1,
        "codec_h264_yuv420p": vs["codec_name"] == "h264" and vs["pix_fmt"] == "yuv420p",
        "resolution": f"{vs['width']}x{vs['height']}" == f"{W}x{H}",
    }
    qa = dict(keyword=kw, out=os.path.abspath(a.out), fonts=[p for p, _ in fonts],
              keyword_box_xywh=[kx, ky, kw_w, kw_h], fps=fps, frames=N, duration=a.duration,
              shots=n_shots, shot_sources=[srcs[i].get("id") for i in order],
              unique_sources=len({srcs[i].get("url") for i in order}), passages=len(srcs),
              blur=a.blur, radial_spin_blur_implemented=False,
              audio=dict(peak_dbfs=round(peak_db, 2), rms_dbfs=round(rms_db, 2)),
              luma=dict(min=min(cut_luma), max=max(cut_luma), max_jump=round(max(luma_jumps or [0]), 2)),
              video=dict(codec=vs["codec_name"], pix_fmt=vs["pix_fmt"], w=vs["width"], h=vs["height"],
                         r_frame_rate=vs["r_frame_rate"], duration=pj["format"]["duration"]),
              checks=checks, passed=all(checks.values()), per_frame=per_frame)
    (qa_dir / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=1), encoding="utf-8")
    (qa_dir / "sources_used.json").write_text(json.dumps(
        [{k: v for k, v in s.items() if not k.startswith("_")} for s in srcs], ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": qa["out"], "qa_dir": str(qa_dir.resolve()), "passed": qa["passed"], "checks": checks},
                     ensure_ascii=False, indent=1))
    sys.exit(0 if qa["passed"] else 2)


if __name__ == "__main__":
    main()

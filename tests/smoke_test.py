#!/usr/bin/env python3
"""Offline smoke test. Uses SYNTHETIC TEST FIXTURE images only (not real sources,
not deliverable material). Renders zh / en / mixed clips and asserts QA passes.
Run:  python tests/smoke_test.py   [--full-zh = one 1920x1080 zh clip]   (needs ffmpeg + a CJK-capable font)
"""
import json, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import render  # noqa

CASES = [("zh", "电子文盲"), ("en", "digital illiteracy"), ("mixed", "AI 文盲 2.0「测试」")]


def fixture(path, kw, filler, fonts, seed):
    W, H = 1400, 900
    im = Image.new("RGB", (W, H), (255, 255, 255)); d = ImageDraw.Draw(im)
    small = ImageFont.truetype(fonts[0][0], 22)
    d.rectangle([0, 0, W, 40], fill=(230, 230, 230))
    d.text((12, 8), "SYNTHETIC TEST FIXTURE - not a real page", font=small, fill=(120, 0, 0))
    f = ImageFont.truetype(fonts[0][0], 30)
    y = 90 + seed * 37
    for i in range(14):
        d.text((60, y + i * 46), (filler * 6)[seed + i: seed + i + 40], font=f, fill=(40, 40, 40))
    pre = (filler * 3)[seed: seed + 6]
    ty = 120 + 46 * (6 + seed % 3)
    d.rectangle([40, ty - 6, W - 40, ty + 40], fill=(255, 255, 255))
    d.text((60, ty), pre, font=f, fill=(40, 40, 40))
    x0 = 60 + d.textlength(pre, font=f)
    for fp, cm in fonts:  # draw kw with first font covering all (fallback: main font)
        if all(ord(c) in cm or c.isspace() for c in kw):
            kf = ImageFont.truetype(fp, 30); break
    else:
        kf = f
    bb = d.textbbox((x0, ty), kw, font=kf)
    d.text((x0, ty), kw, font=kf, fill=(40, 40, 40))
    d.text((bb[2] + 4, ty), pre[::-1], font=f, fill=(40, 40, 40))
    im.save(path)
    return [bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1]]


def main():
    full = "--full-zh" in sys.argv  # default 1920x1080/30fps/4s, Chinese case only
    cases = [CASES[0]] if full else CASES
    size = [] if full else ["--width", "960", "--height", "540"]
    tmp = Path(tempfile.mkdtemp(prefix="kwflash_test_"))
    ok = True
    for name, kw in cases:
        fonts = render.pick_fonts(kw + "SYNTHETICabc测试")
        d = tmp / name; d.mkdir()
        srcs = []
        for i in range(4):
            p = d / f"fixture_{i}.png"
            bb = fixture(p, kw, "合成测试文本 synthetic filler text ", fonts, i)
            srcs.append(dict(id=f"synthetic-{i}", url=f"synthetic://fixture/{i // 2}", title="SYNTHETIC TEST FIXTURE",
                             image=p.name, keyword_bbox=bb, note="SYNTHETIC TEST FIXTURE"))
        (d / "manifest.json").write_text(json.dumps(dict(keyword=kw, sources=srcs), ensure_ascii=False), encoding="utf-8")
        out = d / "clip.mp4"
        r = subprocess.run([sys.executable, str(ROOT / "scripts/render.py"), "--keyword", kw, "--manifest",
                            str(d / "manifest.json"), "--out", str(out)] + size,
                           capture_output=True, text=True)
        qa = json.loads((d / "clip_qa/qa.json").read_text(encoding="utf-8")) if (d / "clip_qa/qa.json").exists() else {}
        print(f"[{name}] {kw!r} rc={r.returncode} passed={qa.get('passed')} box={qa.get('keyword_box_xywh')} "
              f"video={qa.get('video')} audio={qa.get('audio')} fonts={qa.get('fonts')}")
        if r.returncode != 0:
            print(r.stdout[-1500:], r.stderr[-1500:]); ok = False
    # bbox-missing must fail loudly
    bad = tmp / "bad.json"
    bad.write_text(json.dumps({"keyword": "x", "sources": [{"id": "a", "image": "none.png"}]}))
    r = subprocess.run([sys.executable, str(ROOT / "scripts/render.py"), "--keyword", "x", "--manifest", str(bad),
                        "--out", str(tmp / "bad.mp4")], capture_output=True, text=True)
    miss_ok = r.returncode != 0 and "keyword_bbox" in r.stderr
    print("[missing-bbox] rejected:", miss_ok); ok &= miss_ok
    print("ALL PASSED" if ok else "FAILURES", "| artifacts:", tmp)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

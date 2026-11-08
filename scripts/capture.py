#!/usr/bin/env python3
"""OPTIONAL capture helper for keyword-matchcut-intro (runs on the agent's own machine).

Searches public Bing/Google for the quoted keyword (no login), opens result pages,
finds keyword occurrences with DOM Range.getBoundingClientRect, screenshots the
viewport and writes manifest.json with keyword_bbox in image pixels.

It NEVER fabricates data. On captcha / 429 / block / consent wall it stops that
engine/page immediately and records it in capture_report.json -> pending[].
Automated capture can be unreliable; native browser screenshots + manually
measured keyword_bbox are an equally valid manifest input.

  pip install playwright && python -m playwright install chromium
  python scripts/capture.py --keyword "电子文盲" --outdir work --max-pages 6
"""
import argparse, json, re, sys, time, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

BLOCK_PAT = re.compile(r"captcha|unusual traffic|are you a robot|verify you are human|access denied|"
                       r"异常流量|人机验证|/sorry/|challenge", re.I)

FIND_JS = r"""
(kw) => {
  const out = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) {
    const t = n.nodeValue; let i = t.indexOf(kw);
    while (i >= 0 && out.length < 12) {
      const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + kw.length);
      const rects = r.getClientRects(); const b = r.getBoundingClientRect();
      const st = getComputedStyle(n.parentElement);
      if (rects.length === 1 && b.width > 4 && b.height > 6 && st.visibility !== 'hidden' && st.display !== 'none')
        out.push({x: b.left + scrollX, y: b.top + scrollY, w: b.width, h: b.height,
                  ctx: t.slice(Math.max(0, i - 20), i + kw.length + 20)});
      i = t.indexOf(kw, i + kw.length);
    }
  } return out;
}"""


def blocked(page, resp):
    if resp is not None and resp.status in (403, 429, 503):
        return f"HTTP {resp.status}"
    try:
        txt = (page.url + " " + page.title() + " " + page.inner_text("body")[:3000])
    except Exception:
        return None
    m = BLOCK_PAT.search(txt)
    return f"challenge detected: {m.group(0)!r}" if m else None


def shoot(page, kw, outdir, sid, url, kind, sources, max_per_page):
    hits = page.evaluate(FIND_JS, kw)
    dpr = page.evaluate("devicePixelRatio")
    vh = page.viewport_size["height"]
    taken, last_y = 0, -1e9
    for h in hits:
        if taken >= max_per_page or abs(h["y"] - last_y) < vh * 0.6:
            continue
        page.evaluate("y => window.scrollTo(0, Math.max(0, y))", h["y"] - vh * 0.45)
        time.sleep(0.4)
        cur = page.evaluate(FIND_JS, kw)  # re-measure after scroll (viewport coords)
        sy = page.evaluate("scrollY")
        vis = [c for c in cur if 0 <= c["y"] - sy and c["y"] - sy + c["h"] <= vh]
        if not vis:
            continue
        c = min(vis, key=lambda c: abs(c["y"] - h["y"]))
        img = outdir / "shots" / f"{sid}_{taken}.png"
        page.screenshot(path=str(img))
        sources.append(dict(id=f"{sid}_{taken}", url=url, title=page.title(), kind=kind, image=f"shots/{img.name}",
                            keyword_bbox=[round(c["x"] * dpr, 1), round((c["y"] - sy) * dpr, 1),
                                          round(c["w"] * dpr, 1), round(c["h"] * dpr, 1)],
                            context=c["ctx"], captured_at=datetime.now(timezone.utc).isoformat(),
                            capture_method="playwright-dom-range"))
        taken += 1; last_y = h["y"]
    return taken


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--outdir", default="work")
    ap.add_argument("--engines", default="bing,google")
    ap.add_argument("--max-pages", type=int, default=6)
    ap.add_argument("--per-page", type=int, default=2)
    ap.add_argument("--lang", default="zh-CN")
    ap.add_argument("--headful", action="store_true")
    a = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright not installed: pip install playwright && python -m playwright install chromium")
    kw = a.keyword; outdir = Path(a.outdir); (outdir / "shots").mkdir(parents=True, exist_ok=True)
    q = urllib.parse.quote(f'"{kw}"')
    serps = {"bing": f"https://www.bing.com/search?q={q}", "google": f"https://www.google.com/search?q={q}&hl={a.lang}"}
    sources, pending, links = [], [], []
    with sync_playwright() as p:
        br = p.chromium.launch(headless=not a.headful)
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5, locale=a.lang)
        page = ctx.new_page()
        for eng in [e.strip() for e in a.engines.split(",") if e.strip() in serps]:
            url = serps[eng]
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30000); time.sleep(1.5)
            except Exception as e:
                pending.append(dict(url=url, reason=f"load error: {e}")); continue
            why = blocked(page, resp)
            if why:
                pending.append(dict(url=url, reason=why, action="open manually in a real browser; do not bypass"))
                continue
            shoot(page, kw, outdir, f"{eng}_serp", url, "search_results", sources, 1)
            sel = "li.b_algo h2 a" if eng == "bing" else "div#search a:has(h3)"
            for el in page.query_selector_all(sel)[:10]:
                href = el.get_attribute("href")
                if href and href.startswith("http") and href not in links:
                    links.append(href)
        for url in links[: a.max_pages]:
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30000); time.sleep(1.5)
            except Exception as e:
                pending.append(dict(url=url, reason=f"load error: {e}")); continue
            why = blocked(page, resp)
            if why:
                pending.append(dict(url=url, reason=why, action="skipped; not bypassed")); continue
            n = shoot(page, kw, outdir, f"p{len(sources)}", page.url, "article", sources, a.per_page)
            if n == 0:
                pending.append(dict(url=url, reason="exact keyword not found as single-line visible text"))
        br.close()
    (outdir / "manifest.json").write_text(json.dumps(dict(keyword=kw, sources=sources), ensure_ascii=False, indent=1), encoding="utf-8")
    rep = dict(keyword=kw, captured=len(sources), distinct_urls=len({s["url"] for s in sources}), pending=pending,
               ok=len(sources) >= 3 and len({s["url"] for s in sources}) >= 2)
    (outdir / "capture_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if not rep["ok"]:
        print("NOT ENOUGH real captures (need >=3 passages, >=2 urls). Add native-browser screenshots with measured "
              "keyword_bbox to manifest.json, or report to the user. Do not fabricate.", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()

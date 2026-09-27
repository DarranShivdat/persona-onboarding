"""Capture public reference screenshots of Persona's marketing pages (analysis only).
Usage: python3 docs/design/tools/capture_refs.py
Writes docs/design/references/<name>@<viewport>.png (+ fold crops) and a text dump for notes.
Public pages only; no forms are touched."""
import pathlib, sys
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[1] / "references"
PAGES = {
    "home": "https://yourpersona.com",
    "band": "https://yourpersona.com/band",
    "appstore": "https://apps.apple.com/us/search?term=persona%20your%20ai",
}
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}

def launch(p):
    """Prefer bundled Chromium; fall back to the system Chrome channel if the cache is stale."""
    try:
        return p.chromium.launch(headless=True)
    except Exception:
        return p.chromium.launch(headless=True, channel="chrome")

def main(names):
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = launch(p)
        for name, url in PAGES.items():
            if names and name not in names:
                continue
            for vp, (w, h) in VIEWPORTS.items():
                ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1,
                                    is_mobile=(vp == "mobile"))
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="networkidle", timeout=45000)
                except Exception as e:
                    print(f"[warn] {name}@{vp}: {e}")
                page.wait_for_timeout(2500)
                # scroll through to trigger lazy content
                ht = page.evaluate("document.body.scrollHeight")
                for y in range(0, ht, h):
                    page.evaluate(f"window.scrollTo(0,{y})"); page.wait_for_timeout(250)
                page.evaluate("window.scrollTo(0,0)"); page.wait_for_timeout(800)
                page.screenshot(path=str(OUT / f"{name}-fold@{vp}.png"))
                page.screenshot(path=str(OUT / f"{name}-full@{vp}.png"), full_page=True)
                if vp == "desktop":
                    (OUT / f"{name}.txt").write_text(page.inner_text("body"))
                    styles = page.evaluate("""() => {
                      const pick = s => { const e=document.querySelector(s); if(!e) return null;
                        const c=getComputedStyle(e); return {sel:s,font:c.fontFamily,size:c.fontSize,
                        weight:c.fontWeight,color:c.color,bg:c.backgroundColor,ls:c.letterSpacing,lh:c.lineHeight}; };
                      return ['body','h1','h2','h3','p','a','button'].map(pick).filter(Boolean);
                    }""")
                    (OUT / f"{name}.styles.txt").write_text("\n".join(map(str, styles)))
                print(f"ok {name}@{vp} h={ht}")
                ctx.close()
        b.close()

if __name__ == "__main__":
    main(sys.argv[1:])

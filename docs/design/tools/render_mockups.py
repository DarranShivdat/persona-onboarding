"""Render docs/design/mockups/index.html to <state>@<viewport>.png.
Usage: python3 docs/design/tools/render_mockups.py [state ...]"""
import pathlib, sys
from playwright.sync_api import sync_playwright

MOCK = pathlib.Path(__file__).resolve().parents[1] / "mockups"
REQUIRED = ["landing", "chat-agent-name", "call-offer", "call-ringing", "call-connected",
            "call-reconnecting", "gmail-card-idle", "gmail-card-connected", "gmail-card-error",
            "graduation", "welcome-back"]
EXTRA = ["call-muted", "call-ended", "gmail-card-connecting", "gmail-card-wrong-account", "mic-denied"]
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}

def launch(p):
    try:
        return p.chromium.launch(headless=True)
    except Exception:
        return p.chromium.launch(headless=True, channel="chrome")

def main(states):
    states = states or REQUIRED + EXTRA
    with sync_playwright() as p:
        b = launch(p)
        for vp, (w, h) in VIEWPORTS.items():
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            for s in states:
                page.goto(f"{(MOCK / 'index.html').as_uri()}?state={s}&vp={vp}")
                page.wait_for_timeout(150)
                page.screenshot(path=str(MOCK / f"{s}@{vp}.png"))
                print("ok", s, vp)
            ctx.close()
        b.close()

if __name__ == "__main__":
    main(sys.argv[1:])

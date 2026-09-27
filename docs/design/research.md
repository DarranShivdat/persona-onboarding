# Persona: visual language research (DESIGN-001)

Captured 2026-09-26 with Playwright (`python3 docs/design/tools/capture_refs.py`) from public
pages only. Nothing was signed up for, logged into, or submitted. The screenshots are for
analysis only and never ship (see CONSTRAINTS).

| Page | Desktop 1440×900 | Mobile 390×844 |
|---|---|---|
| yourpersona.com (home) | [fold](references/home-fold@desktop.png) · [full](references/home-full@desktop.png) | [fold](references/home-fold@mobile.png) · [full](references/home-full@mobile.png) |
| yourpersona.com/band | [fold](references/band-fold@desktop.png) · [full](references/band-full@desktop.png) | [fold](references/band-fold@mobile.png) · [full](references/band-full@mobile.png) |

Section crops (desktop): [status tiles](references/band-status-tiles@desktop.png),
["Just say what you need"](references/band-just-say@desktop.png),
[privacy cards](references/band-privacy-cards@desktop.png),
[spec grid](references/band-spec-grid@desktop.png).
Raw text and computed styles: `references/{home,band}.txt`, `references/{home,band}.styles.txt`.

**App Store listing:** I couldn't confidently identify "Persona - Your AI" (Iris Assistant,
Inc.). Search turned up several similarly named apps (Iris, PersonaAI, Persona Self-Discovery)
from other companies, so no App Store screenshots were captured. That means the in-app
onboarding hasn't been studied yet. See OPEN_DESIGN_QUESTIONS in the packet report.

## What their product looks like

**Overall.** It follows Apple's product-page style: white canvas, a system font stack, huge
tight-tracked headlines, mid-grey supporting copy, and product photos on white. Chrome is
minimal. There's lots of air and almost no ornament.

**Type.** The stack is `-apple-system, system-ui, "SF Pro Display", "SF Pro Text", Inter, ...`.
There's one family throughout.
- The home hero h1 is 56/56 at weight 500, tracking −0.84px. On /band, the h1 is 64/68 with
  tracking −2.2px and the h2 is 60/65.
- Body copy is 16–17px at weight 400. Supporting paragraphs are grey (`#6E6E73`) at 17/25.5.
- Headlines are sentence case and read like spoken sentences ("Just say what you need.",
  "Every detail, on purpose."). They sometimes italicize one word ("to-do to *ta-da*",
  "Adapts to *you*", "most *private* one"). We deliberately don't copy that tic (§ spec
  Copy tone).

**Color.**
- Ink is `#1D1D1F`, secondary text is `#6E6E73`, and the canvas is white.
- Panels use Apple-style mist grey (≈`#F5F5F7`), and buttons use a translucent grey
  (`oklab(... / .06)`).
- Primary CTAs are black pills ("Pre-order Band", "Start on iMessage"). The home CTA is a
  white pill with a green iMessage glyph.
- There's only one saturated accent in the product itself: the Band's **green LED ring**.
  Green also appears as the thin progress underline on the status tiles. Privacy cards use a
  blue lead-in line ("Independently audited.") above a black line.

**Shape.** Pills for every button. Cards and tiles have 16–24px radii with no border and
mist fills. The footer is a large rounded card. Shadows are almost absent; the only real
depth is the soft float under the phone and Band photos.

**Imagery.**
- An iPhone showing an iMessage thread with "Persona" is the hero on the home page, so
  their core UX is literally a text thread.
- The Band is shown on a wrist with the glowing green ring.
- The status-tile collage ("Order placed / Dinner arrives at 7:40pm", "Reply drafted /
  Waiting for your OK", "Dentist called / Tuesday 9:15am, booked") has a small app icon, a
  two-line title/detail, and a green underline. It's their clearest pattern for "a task
  moved forward".

**Voice and tone.**
- Confident, casual, and a bit cheeky ("Made to get sh\*t done.", "From to-do to ta-da.").
- Short declaratives: "It moves before you ask. Nothing happens without your yes." "Talk to
  it the way you'd talk to your friend." "No prompts to learn, nothing to set up."
- **Consent is a brand pillar.** "Waiting for your OK", "Nothing happens without your yes",
  and "comes back with confirmations" all repeat the idea. Our Gmail consent copy should use
  the same words ("your OK").
- Privacy copy uses short paired lines ("Never sold, never traded. / Not a product, not to
  anyone."). We must **not** repeat their security claims (SOC 2, AES-256) because
  product-facts.md forbids it.

**Onboarding patterns we can see.**
- "Get Started", "Start on iMessage" and "Create your Persona today" all lead into a text
  thread. There are no forms on the marketing site.
- The FAQ answers "Do I need to set it up or learn it?" by stressing zero setup. Our
  onboarding should feel like the first texts with your new assistant, not a settings wizard.
- Use cases are presented as tabs ("Money Saver", "Appointment Booker", "Meeting
  Notetaker", "Life Planner"). These can seed the example chips for the "need" question.

## What we take (and what we don't)

| Take (their language) | Make our own (original) |
|---|---|
| System font stack, big tight headlines, sentence case | Our own type scale (17px chat body, 30px call name) |
| White canvas, `#1D1D1F` ink, `#6E6E73` secondary, mist panels | Graphite call surface so the call reads as a call |
| Black pill primary CTAs | Our own button set (primary, secondary, quiet) |
| Text-thread bubbles as the core UX | Our own bubble blue (`#1F6FEB`, not iMessage's), agent grey |
| Green "live" ring as the product's signature | **The ring** as our voice indicator, drawn in CSS (no photo, no logo) |
| Status tile with a green underline | The 4-item checklist in the top bar |
| "Your OK" consent vocabulary | Gmail card copy written from product-facts.md |

No logos, glyphs, photos or product renders from yourpersona.com are used in mockups. The
wordmark in the mockups is just the word "Persona" set in the system font.

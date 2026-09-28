# DESIGN-002 — Copy tone pass

Tone: warm, brief and confident, like a capable friend texting. Keep one idea per bubble,
use no "Step n", "Please enter" or "Oops", and don't add exclamation marks after the
greeting. Every product or privacy claim must trace to `docs/product-facts.md`.

**Status legend:** `keep` means the line is already right; `change` means a line the
Monday polish packet should update. In the Source column, "brain" marks a template or
phrasing target the agent says (spec §5, `flow.yaml` `why:`), and "UI" marks a string
rendered by `apps/web`.

## A. Agent-authored lines (brain templates / phrasing targets)

| # | Moment | Source | Current | Proposed | Status |
|---|---|---|---|---|---|
| A-01 | greeting | brain (spec §5) | "Hi! I'm your new assistant. I'll help with email, your calendar, and the everyday stuff." / "First things first: what would you like to call me?" | same, plus suggestion chips Juno · Atlas · Surprise me (REPORT DQ-04) | keep |
| A-01s | greeting (stub only) | `e2e/stub-agent.mjs:131` | "Hi! I'm your new Persona assistant. What would you like to call your assistant?" | Use A-01 verbatim so e2e shots match what reviewers see. ("call your assistant" is odd when the assistant itself is asking.) | change |
| A-02 | agent name captured | brain | "Juno it is. I like it." | keep | keep |
| A-03 | agent name steer-back (idk / nonsense) | brain | "No pressure. Pick anything, or I can suggest one. Juno? Atlas?" | "No pressure. How about Juno, or Atlas? Anything you like works." | change |
| A-04 | agent name refusal | brain | "Assistant works. You can rename me anytime." | keep | keep |
| A-05 | call offer | brain | "The rest is easier out loud. Want to hop on a quick call? Or we can keep texting, totally fine either way." | "The rest goes faster out loud. Quick call? Texting works just as well." | change (shorter, and one bubble fits beside the card) |
| A-06 | call declined | brain | "Texting it is. What should I call you?" | keep | keep |
| A-07 | call answer (voice) | brain | "Hey, it's Juno! What should I call you?" | keep | keep |
| A-08 | user name ask (text) | stub `ASK.user_name` | "And what should I call you?" | "What should I call you?" (no leading "And" when it opens a turn) | change |
| A-09 | user name captured | brain | "Nice to meet you, Maya." | keep | keep |
| A-10 | user name refusal | brain | "Totally fine, I'll just say hi. So what could I take off your plate?" | "Totally fine. So what could I take off your plate?" | change ("I'll just say hi" reads oddly) |
| A-11 | need ask | brain | "What's one thing you'd love help with? Email, scheduling, errands, anything." | keep | keep |
| A-11o | need captured out of order | brain | "Got it, inbox rescue is on the list. And your name?" | keep | keep |
| A-11s | need steer-back (off-topic / joke) | brain | "Ha, fair. Really though, what's one thing that'd make this week easier?" | keep | keep |
| A-11u | need unsure | brain (`flow.yaml` goal) | none (3 examples) | "Most people start with one of these: clearing the inbox, keeping the calendar sane, or chasing replies. Any of those?" | new |
| A-12 | Gmail ask (text) | brain (spec §5) | "To help with your inbox I'll need Gmail. I just put a button on your screen." (text: "…Tap Connect below.") | text: "To help with your inbox I'll need Gmail. Tap **Continue with Google** below." Voice: keep "I just put a button on your screen." | change (the button label is "Continue with Google", REPORT DQ-09) |
| A-12s | Gmail ask (stub) | `stub-agent.mjs:74` | "Last step: connect your Gmail with the button below." | same as A-12 | change ("Last step" is stepper talk) |
| A-13 | Gmail value (after first refusal) | brain + `flow.yaml` `why` | "Fair. It's how I read and sort your email, and I never send anything without your OK. Want to connect now or later?" | "Fair. Gmail is how I read and sort your email, and nothing gets sent or changed without your OK. Connect now, or later?" | change (wording matches the facts file exactly) |
| A-14 | Gmail second refusal | brain | "No problem. I'll leave a reminder for later." | keep | keep |
| A-15 | Gmail waiting (call) | brain | "Take your time. I'll hang on while you sign in with Google." | keep | keep |
| A-16 | Gmail connected | brain | "Got it, connected as maya.r@gmail.com." | keep | keep |
| A-17 | Gmail failed | brain | "Looks like Google didn't finish. Want to try again, or skip it for now? You can always connect later." | "No luck with Google that time. Try again, or skip for now?" (the card already explains, DQ-10) | change |
| A-17b | Gmail blocked (not an invited test account) | brain | none | "Google's only letting invited accounts in during this trial. Want to try a different Google account, or skip for now?" | new (facts: testing mode, invited accounts only) |
| A-18 | **privacy answer** ("what do you do with my email?") | brain (intent `privacy_question`) | none | "Good question. You sign in with Google, so I never see your password. I read and organize your email and can send for you, but nothing is sent or changed without your OK. You can disconnect anytime." Then re-ask the current node. | new (every clause is in product-facts.md; don't say "encrypted" unless asked about storage) |
| A-18b | privacy (storage follow-up) | brain | none | "Your Google tokens are stored encrypted, and disconnecting deletes them." | new (trial build only; never claim anything about Persona production) |
| A-18v | privacy (voice) | brain | none | "Calls go through speech services that turn your voice into text and back. That's all they're used for here." | new. **Hold** "that's all they're used for" until Darran fills in the retention line. Until then, say only the first sentence. |
| A-19 | generic refusal ("I'd rather not say") | brain (intent `refuse_slot`) | none | "No problem, we can skip that." Then move to the next open item. | new |
| A-20 | abuse / prompt injection | brain | none | "Let's keep it friendly. So, {current ask}" / "I'll stick to getting you set up. {current ask}" | new |
| A-21 | reconnect within grace | brain | "Sorry, we got cut off. You were telling me about your inbox." | "We got cut off. You were telling me about your inbox." | change (errors don't apologize, spec §5) |
| A-22 | hangup resume (chat) | brain | "We got cut off, no worries. I still have your name and what you need. Only Gmail is left. Call back, or connect it right here?" | "We got cut off, no worries. I kept your name and what you need, so only Gmail's left. Call back, or connect it right here?" | change (3 sentences, not 4) |
| A-23 | silence floor (call) | brain | "Still there? Take your time. You can also type if that's easier." | keep | keep |
| A-24 | insists on finishing early | brain | "Sure, we can wrap up. I'll keep what I have and remind you about the rest." | keep | keep |
| A-25 | value demo | brain (`flow.yaml value_demo`) | none | "Here's my plan: I'll start with {need}, and check with you before anything gets sent." | new (no invented inbox facts) |
| A-26 | graduation | brain / UI h1 | "You're all set, Maya." | keep | keep |
| A-26s | graduation (stub) | `stub-agent.mjs:75` | "You're all set — let's get to work." | "You're all set, {name}." | change (no dash) |
| A-27 | welcome back | brain | "Welcome back, Maya! We're nearly done. Just Gmail left, so I can start on that inbox." | "Welcome back, Maya. Just Gmail left, then I can start on that inbox." | change (no "!" after the greeting, one idea) |
| A-28 | stub echo | `stub-agent.mjs:85,98` | "Got it: {text}. …" | Make sure the real brain never echoes raw input like this. The stub should say "Got it." only. | change (stub) |

## B. UI strings (apps/web)

| # | Where | Current | Proposed | Status |
|---|---|---|---|---|
| U-01 | landing h1 | "Meet the assistant that gets things done." | keep (+ `text-wrap: balance`, DQ-06) | keep |
| U-02 | landing lede | "Give it a name, tell it what you need, and connect Gmail. Text or talk, whichever you like." | keep | keep |
| U-03 | landing fine print | "About two minutes. You can stop anytime." | keep | keep |
| U-04 | composer placeholder (agent name) | "Type a name…" | keep | keep |
| U-05 | composer placeholder (in call) | "Type instead of talking…" | keep | keep |
| U-06 | offer card (`agent-state.ts:87-88`) | "Talk it through with Juno" / "A quick call, about two minutes." | "Talk it through with Juno" / "About a minute. Typing works too." | change (DQ-12) |
| U-07 | welcome-back offer | "Finish on a quick call" / "Under a minute. Or connect right here." | keep | keep |
| U-08 | top bar right | "Setting up" | drop it, or show the user's name once filled | change (P2, DQ-14) |
| U-09 | Gmail card sub | "So Juno can work in your inbox" | keep | keep |
| U-10 | Gmail card heads-up | "Heads up: this is a trial, so Google will say it hasn’t verified the app. Choose Continue to go on." | "Heads up: this is a trial, so Google will say it hasn’t verified the app. Choose **Continue**, then tick the Gmail boxes (or **Select all**). Only invited Google accounts can connect for now." | change (DQ-02) |
| U-11 | Gmail card fine print | "You can disconnect Gmail anytime in Settings." | "You can disconnect Gmail anytime." | change (DQ-03) |
| U-12 | Gmail error alert + hint | "Google didn’t finish signing in. The window may have closed, or access wasn’t allowed." / "Try again and choose Continue on the “unverified app” screen, then Allow." | "Google didn’t finish signing in. The window may have closed, access wasn’t allowed, or this account isn’t on the trial list." / "Try again: choose **Continue** on the “unverified app” screen, tick the Gmail boxes, then **Continue**." | change |
| U-13 | Gmail connecting | "Waiting for Google…" / "Open the Google window again" | keep | keep |
| U-14 | wrong account | "Wrong account? Disconnect it and pick another one on Google." | "Wrong account? Switch to another one on Google." | change (shorter) |
| U-15 | partial grant | "Connected without permission to send email. Juno can still do the rest. Reconnect anytime to allow it." | "Connected, but without permission to send email. Juno can still read and organize. Allow sending anytime." | change (says what still works) |
| U-16 | home lede | "Juno is ready. Here’s what it knows so far." | keep | keep |
| U-17 | home deferred: Gmail | "Connect Gmail when you’re ready" / "Juno needs it to sort your inbox. Takes a minute." | keep | keep |
| U-18 | home deferred: need / name (`agent-state.ts:58-59`) | "Tell me what to start on" / "Tell me your name" | "Tell Juno what to start on" / "Tell Juno your name" | change (DQ-11) |
| U-19 | home deferred: name reason | "So Juno knows who it’s working for." | keep | keep |
| U-20 | mic denied (`api-driver.ts:40`) | "I can’t hear you yet: your browser blocked the microphone. To allow it, click the icon at the left of the address bar, set Microphone to Allow, then call again." | "Your browser’s blocking the mic, so I can’t hear you. Allow microphone access for this site in your browser’s settings, then call again." | change (DQ-07, works on Safari and iOS) |
| U-21 | mic missing (`:41`) | "I can’t hear you yet: I couldn’t find a microphone. Plug one in or check your sound settings, then call again." | "I can’t find a microphone. Plug one in or check your sound settings, then call again." | change |
| U-22 | unsupported (`:42`) | "I can’t hear you yet: this browser can’t make calls from here. Try the latest Chrome, Safari, or Firefox, then call again." | "This browser can’t make calls here. The latest Chrome, Safari, or Firefox can." | change |
| U-23 | mic fallback (`:51-55`) | "Or we can just keep texting. So, what’s one thing you’d love a hand with?" | keep | keep |
| U-24 | call failures (`:220-257`) | "Couldn’t reach the call line. Your progress is saved, so let’s keep texting or try again." (and variants) | keep | keep |
| U-25 | send failure stamp (`:191`) | "Couldn’t send that. Check your connection and try again." | keep | keep |
| U-26 | ringing hint | "Captions appear here when Juno answers." | keep | keep |
| U-27 | reconnecting caption | "The line dropped. Trying to reconnect." | keep | keep |
| U-28 | call elsewhere | status "On a call in another tab" and an empty captions box | add caption: "This call is open in another tab. Take it over here, or keep typing." | change (DQ-08) |

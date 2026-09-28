# Reviewer guide: Persona onboarding (trial build)

**Try it:** https://persona-onboarding-darran.vercel.app

This is a hosted, conversational onboarding for [Persona](https://yourpersona.com), a
personal AI assistant that gets things done: you text it, and it helps with your email,
calendar, and everyday tasks. It was built for the Persona CTO trial. It's a separate
trial build, not Persona's production service.

The onboarding collects four things in a conversation, not a form:

| # | What | How |
|---|---|---|
| 1 | A name for your assistant | text chat, before the call |
| 2 | Your name | call or text |
| 3 | A connected Gmail | Google sign-in (OAuth) card, on the call or in chat |
| 4 | One thing you'd like help with | call or text |

You can answer in any order. If you say two things at once ("I'm Maya and my inbox is a
mess"), both get recorded and those questions are skipped. Once the assistant knows what
you need, or if you insist on stopping, you can wrap up early. Anything you skipped is
kept as a reminder.

## Before you start: Gmail

Any Google account can connect. The Google app is published but not yet verified, so
Google shows an "unverified app" notice first. You can also skip Gmail; everything else
works without it.

On the Google screens:
1. Google says it **hasn't verified this app**. Choose **Continue**.
2. On the permissions screen, **tick the Gmail boxes (or Select all)**, then **Continue**.
   If you leave the send box unticked, Gmail still connects, just without permission to send.
3. You're returned to the conversation, and the card shows which account you connected.

What access is requested and why: Persona asks for **read and write** access to Gmail.
It reads your email to understand what needs attention, organizes it (labels, archive,
mark as read, drafts), and sends email for you, but only after you say OK. Nothing is
sent or changed in your inbox without your explicit OK. You sign in with Google, so the
assistant never sees your password. Tokens are stored encrypted, and you can disconnect
at any time, which revokes access and deletes the stored tokens.

## 5-minute happy path

1. Open https://persona-onboarding-darran.vercel.app and start. Use a laptop with Chrome,
   Safari, or Firefox and a working mic.
2. **Name the assistant** by typing it, for example "Juno", or tap a suggestion if one is shown.
3. **Take the call.** Accept the "Talk it through with Juno" offer and allow microphone
   access. Captions appear as the assistant speaks.
4. **Say your name.**
5. **Connect Gmail.** The assistant puts a **Continue with Google** card on screen. Follow
   the Google steps above, and the call waits while you sign in.
6. **Say one thing you need help with**, for example "keeping my inbox under control".
7. **Graduate.** You land on "You're all set, {name}" with what the assistant knows so far.

Prefer not to talk? Decline the call ("texting works just as well"). The same four steps
run in chat, backed by the same brain and the same saved state.

**Start over / `?reset=1`.** Your progress is saved in this browser, so a reload brings you
back where you left off, including the "You're all set" screen. To test again from scratch,
tap **Start over** in the top-right corner (it's there mid-flow and on the graduation screen)
and confirm, or open https://persona-onboarding-darran.vercel.app/?reset=1. Either way, the
saved session is forgotten and you land on a fresh "name your assistant" step.

## Things worth stress-testing

These are designed behaviours. Please try to break them.

| Try | What should happen |
|---|---|
| **Hang up mid-call**, then reload or come back | The conversation resumes where you left off. Only the answer in progress can be lost, and the chat tells you what's left. |
| Call back after a hangup | It picks up at the same step and doesn't re-ask what it already has. |
| Refuse to give your name, or to connect Gmail | "No problem." It moves on and keeps a reminder. |
| Say something off-topic, a joke, or nonsense | A short, friendly steer back to the current question. |
| Ask "what do you do with my email?" | It gives a plain answer about access, then returns to where you were. |
| Answer out of order | The answer is recorded and that question is skipped. |
| "Let's just finish" at any point | It wraps up early, keeps what it has, and reminds you about the rest. |
| Go quiet on the call | It never leaves dead air. It checks in and offers typing. |
| Talk over the assistant | It stops speaking and listens (barge-in). |
| Type during the call ("Type instead of talking…") | Typed and spoken turns share one conversation. |

## If the call doesn't work

- **Mic blocked or missing:** the page says so and offers to continue by text. Allow
  microphone access for the site in your browser settings, then call again.
- **Network or firewall blocks audio:** calls relay over TURN, so most corporate and
  cellular networks work. If the line still drops, the page says it's reconnecting. If
  that fails, your progress is saved and you can keep texting or try again.
- **Always available:** type in the composer. Nothing about the flow requires voice.

## Intentionally cut (for the deadline)

- **Spelling an email address out loud** (NATO alphabet). On the call, Gmail is connected
  through the on-screen Google card instead, and you can type it if you prefer.
- **Live LLM-judge evaluations in Langfuse.** Conversation tests run with scripted and
  mocked LLMs. Live tracing is optional, and the live judge and dataset sync come after
  the deadline.
- **Automated voice fault injection.** This was replaced by a browser call smoke test
  with fake media and a manual hosted checklist (hangup, dropped Wi-Fi, redial,
  barge-in).

These were **never cut**: the text flow, the browser voice call with TURN, Gmail OAuth
read and write, hangup resume, early graduation, and steer-back.

## Known caveats

- Google shows an **"unverified app"** notice before Gmail connect. See above.
- **Voice** audio is processed by third-party speech services (speech-to-text and
  text-to-speech) to run the conversation.
- This is a trial build. It makes no claims about Persona's production service (pricing,
  certifications, encryption, and so on).

## For engineers

- Code overview: [`README.md`](../README.md) and [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
  Flow spec: `packages/flow/flow.yaml`. Edge-case catalog: `harness/edge-cases.yaml`.
- Design principle: **code owns progress, and the LLM owns phrasing and extraction.** One
  flow engine (`services/agent/agent/brain`) drives both text and voice from a single
  saved state.
- Agent health (server-side service; reviewers don't need it):
  `https://persona-onboarding-agent.fly.dev/health`.
- Spoken walkthrough script: [`docs/WALKTHROUGH.md`](WALKTHROUGH.md).

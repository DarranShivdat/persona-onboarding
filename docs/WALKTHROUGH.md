# Walkthrough script (Loom-style, about 3 to 5 minutes)

A script to read aloud while screen-recording, or to follow on your own.
**Bold** = do this on screen. Plain text = say this. Times are rough.

Setup: a laptop with Chrome, Safari, or Firefox, a working mic, and a Google account
that's on the trial's test-user list (see [REVIEWER.md](REVIEWER.md#before-you-start-gmail-needs-an-invited-account)).
Close other tabs that are using the mic.

---

### 0:00 Intro (about 20s)
**Open https://persona-onboarding-darran.vercel.app**

> This is a conversational onboarding for Persona, a personal assistant you text that
> helps with email, calendar, and everyday tasks. Instead of a form, it collects four
> things in a conversation: a name for the assistant, my name, a connected Gmail, and one
> thing I want help with. Some of it happens in chat, and the rest on a quick browser call.

### 0:20 Name the assistant, by text (about 30s)
**Start. Type `Juno` (or tap a suggestion if one is shown).**

> The agent name is the one thing collected before the call. The assistant confirms it
> and offers a call. Code decides what's been collected and what comes next. The model
> only phrases the reply and pulls answers out of what I say.

### 0:50 Take the call (about 20s)
**Accept "Talk it through with Juno". Allow the microphone.**

> This is a real voice call in the browser over WebRTC, with a TURN relay so it works
> behind strict networks. Captions show what the assistant says.

### 1:10 Fill slots out of order (about 40s)
**Say:** "Hey, I'm {your name}, and honestly my inbox is out of control."

> I just gave two answers in one sentence. Every utterance is checked for all the missing
> pieces, so both my name and my need were recorded and it won't ask for them again.

**Optional: interrupt the assistant mid-sentence.**

> It stops talking when I talk (barge-in).

### 1:50 Steer-back (about 20s)
**Say something off-topic:** "What's the best pizza topping?"

> It gives a short, friendly steer back to what's still open, without a lecture and
> without losing its place.

### 2:10 Gmail card (about 60s)
**When the assistant says it put a button on screen, click "Continue with Google".**

> Gmail connects with Google sign-in, so the assistant never sees my password. It asks
> for read and write access: read to understand the inbox, organize with labels,
> archiving, and drafts, and send, but only after I say OK. Nothing is sent or changed
> without my explicit OK.

**On "Google hasn't verified this app", choose Continue. Tick the Gmail boxes (or
Select all), then Continue.**

> The app is in Google's testing mode for this trial, so only invited accounts can
> connect, and Google shows this notice. The call waits while I sign in.

**Back on the call, the card shows the connected address and the assistant confirms it.**

### 3:10 Graduate (about 20s)
> That's all four. The assistant graduates me.

**Land on "You're all set, {name}".**

> From here, Persona would start on the thing I asked for.

### 3:30 Resilience (optional, about 60s)
**Reload and start a new conversation. After naming the agent, take the call, say your
name, then hang up mid-sentence. Reload the page.**

> Hanging up never loses more than the answer in progress. The chat tells me what's
> left, and I can call back or finish right here in text. Text and voice share one brain
> and one saved state.

**Type:** "let's just finish"

> I can also graduate early at any point. It keeps what it has and reminds me about the
> rest.

### 4:30 Close (about 10s)
> The text flow, voice call, Gmail connection, hangup resume, early finish, and
> steer-back are all live. The reviewer guide covers what was cut and the known caveats.

---

## Backup: if the mic or call fails ("type it" path)

Don't debug on camera. Switch to typing and keep going:

- **Mic blocked:** the page says so and offers to keep texting. Say "No mic today, so
  I'll type it. Same brain, same state," and continue in the composer.
- **Call won't connect or drops:** the page says the progress is saved. Type in the
  composer, or tap call again once. If it drops mid-call, show the resume. That's a
  feature: "We got cut off, and nothing's lost."
- **During a call:** use the "Type instead of talking…" composer for any answer, such as
  an awkward name or a noisy room. Typed and spoken turns go into the same conversation.
- **Gmail 403 `access_denied`:** the account isn't an invited test user. Say "Testing
  mode only allows invited accounts," skip Gmail ("later"), and graduate. The assistant
  keeps a reminder to connect Gmail.
- **Declined the call from the start:** the whole flow (name, Gmail card, need,
  graduation) works in text.

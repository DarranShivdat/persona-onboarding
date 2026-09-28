import type { Metadata } from "next";
import Link from "next/link";
import s from "../public.module.css";

export const metadata: Metadata = {
  title: "Privacy Policy — Persona setup",
  description: "How the Persona setup experience collects, uses, stores and deletes your data, including Google user data.",
};

const UPDATED = "September 27, 2026";

// Public privacy policy (Google OAuth branding "Application privacy policy link").
// Claims must stay consistent with docs/product-facts.md and the actual code paths.
export default function Privacy() {
  return (
    <main className={s.page}>
      <div className={s.wrap}>
        <Link href="/about" className={s.brand}>Persona</Link>
        <h1 className={s.h1}>Privacy Policy</h1>
        <p className={s.lede}>Last updated {UPDATED}. This policy covers the Persona setup
          experience on this website (the “Service”).</p>
        <div className={s.body}>
          <h2 className={s.h2}>What we collect</h2>
          <ul>
            <li><strong>What you tell your assistant</strong>: the text of your setup conversation
              (typed, or transcribed from voice), such as your name, what you want help with, and
              your preferences.</li>
            <li><strong>Voice audio</strong>, only while you are on a call. Audio is streamed for
              real-time speech recognition and speech synthesis. The Service does not save call
              recordings.</li>
            <li><strong>Google account data</strong>, only if you choose to connect Gmail: your
              email address and name, and the access to Gmail you approve (see below).</li>
            <li><strong>Technical data</strong>: a session cookie that keeps your setup
              conversation going across page refreshes, and basic server logs used to keep the
              Service running.</li>
          </ul>

          <h2 className={s.h2}>Google user data</h2>
          <p>If you connect Gmail, you sign in with Google and approve these permissions:</p>
          <ul>
            <li><strong>Read</strong> your email (<code>gmail.readonly</code>), to understand
              your inbox and what needs your attention;</li>
            <li><strong>Organize</strong> your email (<code>gmail.modify</code>): labels, archive,
              mark as read, drafts;</li>
            <li><strong>Send</strong> email on your behalf (<code>gmail.send</code>), only after
              you explicitly say OK to each message;</li>
            <li>your basic profile (<code>openid email profile</code>), to confirm which account
              you connected.</li>
          </ul>
          <p>Nothing is sent or changed in your inbox without your explicit OK. We use Google user
            data only to provide the assistant features you asked for. We do not sell it, use it
            for advertising, or use it to train generalized AI models. People do not read your
            email unless you ask us to for support, it is needed for security or to comply with
            the law.</p>
          <p>Persona’s use and transfer of information received from Google APIs will adhere to
            the <a href="https://developers.google.com/terms/api-services-user-data-policy">Google
            API Services User Data Policy</a>, including the Limited Use requirements.</p>
          <p>Your Google tokens are stored encrypted. You can disconnect Gmail at any time from
            the Service, which revokes our access with Google and deletes the stored tokens. You
            can also remove access at <a href="https://myaccount.google.com/permissions">
            myaccount.google.com/permissions</a>.</p>

          <h2 className={s.h2}>How we use your data</h2>
          <p>To run your setup conversation, remember your answers, and carry out the email tasks
            you approve. Email content is processed only to perform the task you requested.</p>

          <h2 className={s.h2}>Service providers</h2>
          <p>We share data only with providers that run the Service on our behalf, and only as
            needed for that purpose: Anthropic (AI language model), Deepgram (speech recognition
            and backup speech synthesis), Cartesia (speech synthesis), Supabase (database), Fly.io
            (application hosting), Vercel (website hosting) and Cloudflare (call connectivity).
            Google data is sent to the AI model only when it is needed to perform a task you
            asked for.</p>

          <h2 className={s.h2}>Retention and deletion</h2>
          <p>Setup conversations and Gmail connection records are kept for as long as needed to
            provide the Service, and no longer than 30 days after your last activity. Disconnecting
            Gmail deletes the stored Google tokens immediately. To delete your data sooner, contact
            us using the details below.</p>

          <h2 className={s.h2}>Security</h2>
          <p>Data is sent over encrypted connections (HTTPS/TLS), Google tokens are encrypted at
            rest, and database access is restricted to the Service’s servers.</p>

          <h2 className={s.h2}>Children</h2>
          <p>The Service is not directed to children under 13, and we do not knowingly collect
            their data.</p>

          <h2 className={s.h2}>Changes and contact</h2>
          <p>We will update this page if our practices change. Questions or deletion requests:
            email <a href="mailto:darranshivdat1@gmail.com">darranshivdat1@gmail.com</a>.</p>
        </div>
        <footer className={s.foot}>
          <Link href="/about">About Persona</Link>
          <Link href="/">Set up your assistant</Link>
        </footer>
      </div>
    </main>
  );
}

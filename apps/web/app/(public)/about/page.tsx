import type { Metadata } from "next";
import Link from "next/link";
import s from "../public.module.css";

export const metadata: Metadata = {
  title: "Persona — your assistant that gets things done",
  description: "Set up Persona, a personal AI assistant for your email and everyday tasks.",
};

// Public homepage (Google OAuth branding "Application home page"). Static, no session.
export default function About() {
  return (
    <main className={s.page}>
      <div className={s.wrap}>
        <Link href="/" className={s.brand}>Persona</Link>
        <h1 className={s.h1}>A personal assistant that gets things done</h1>
        <p className={s.lede}>
          Persona helps with your email and everyday tasks. This site is the Persona setup
          experience: a short conversation, by voice or text, where your assistant gets to know
          you and, if you choose, connects to your Gmail.
        </p>
        <div className={s.body}>
          <h2 className={s.h2}>What connecting Gmail does</h2>
          <p>
            Connecting uses Google sign-in, so Persona never sees your password. With your
            permission, Persona can read your email to understand what needs attention, organize
            it (labels, archive, mark as read, drafts), and send email for you, but only after
            you say OK. Nothing is sent or changed in your inbox without your explicit OK, and
            you can disconnect at any time.
          </p>
          <p>
            Read our <Link href="/privacy">Privacy Policy</Link> to see exactly what we collect
            and how Google user data is handled.
          </p>
        </div>
        <p style={{ marginTop: "var(--s6)" }}>
          <Link href="/" className={s.cta}>Set up your assistant</Link>
        </p>
        <footer className={s.foot}>
          <span>Persona · <a href="https://yourpersona.com">yourpersona.com</a></span>
          <Link href="/privacy">Privacy Policy</Link>
        </footer>
      </div>
    </main>
  );
}

import Link from "next/link";
import s from "./(public)/public.module.css";

// AUDIT-001: an unknown route says so and offers a way back (Next's default 404 is a dead end).
export default function NotFound() {
  return (
    <main className={s.page}>
      <div className={s.wrap}>
        <Link href="/" className={s.brand}>Persona</Link>
        <h1 className={s.h1}>This page doesn’t exist</h1>
        <p className={s.lede}>The link may be old or mistyped. Your setup is saved, so you can pick up where you left off.</p>
        <p style={{ marginTop: "var(--s6)" }}>
          <Link href="/" className={s.cta}>Set up your assistant</Link>
        </p>
        <footer className={s.foot}>
          <Link href="/about">About Persona</Link>
          <Link href="/privacy">Privacy Policy</Link>
        </footer>
      </div>
    </main>
  );
}

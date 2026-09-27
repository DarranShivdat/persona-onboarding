"use client";
import { forwardRef, useState, type KeyboardEvent } from "react";
import { PhoneIcon, SendIcon } from "./icons";

interface Props {
  placeholder: string;
  callLabel?: string | null;
  onSend: (text: string) => void;
  onCall?: () => void;
}

/** Pill composer: Enter sends, Shift+Enter adds a newline, Escape does nothing (spec §6). */
export const Composer = forwardRef<HTMLTextAreaElement, Props>(function Composer({ placeholder, callLabel, onSend, onCall }, ref) {
  const [text, setText] = useState("");
  const send = () => {
    if (!text.trim()) return;
    onSend(text);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      {callLabel && onCall && (
        <button type="button" className="iconbtn call" aria-label={`Call ${callLabel}`} onClick={onCall} data-testid="composer-call">
          <PhoneIcon />
        </button>
      )}
      <textarea
        ref={ref}
        className="field"
        rows={1}
        value={text}
        placeholder={placeholder}
        aria-label={placeholder.replace(/…$/, "")}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
      />
      <button type="submit" className="iconbtn send" aria-label="Send">
        <SendIcon />
      </button>
    </form>
  );
});

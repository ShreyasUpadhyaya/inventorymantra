"use client";

import { Loader2, Mic, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import {
  browserRecognition,
  chooseMode,
  MAX_RECORDING_MS,
  pickMimeType,
  recognitionLang,
  serverVoiceAvailable,
  transcribe,
  nextVoiceLang,
  VOICE_LANGS,
  type Recognition,
  type VoiceLang,
  type VoiceMode,
} from "@/lib/voice";

const LANG_KEY = "inventorymantra.voiceLang";

function storedLang(): VoiceLang {
  try {
    const value = window.localStorage.getItem(LANG_KEY);
    return value === "en" || value === "hi" ? value : "auto";
  } catch {
    return "auto"; // storage blocked: just use automatic
  }
}

type Props = {
  disabled: boolean;
  recentText: string; // picks Hindi or English for browser recognition
  onText: (text: string) => void; // goes into the message box; never sent from here
  onError: (message: string) => void;
};

/** Click to start talking, click again to stop. The words land in the message box. */
export function MicButton({ disabled, recentText, onText, onError }: Props) {
  const [mode, setMode] = useState<VoiceMode>("none");
  const [state, setState] = useState<
    "idle" | "starting" | "listening" | "transcribing"
  >("idle");
  // read once on the client; nothing renders before the mode is known, so no hydration mismatch
  const [lang, setLang] = useState<VoiceLang>(() =>
    typeof window === "undefined" ? "auto" : storedLang(),
  );
  const recorder = useRef<MediaRecorder | null>(null);
  const recognition = useRef<Recognition | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let live = true;
    const hasRecorder =
      typeof window !== "undefined" && "MediaRecorder" in window;
    serverVoiceAvailable().then((server) => {
      if (live)
        setMode(chooseMode(server, browserRecognition() !== null, hasRecorder));
    });
    return () => {
      live = false;
      recorder.current?.stop();
      recognition.current?.stop();
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  if (mode === "none") return null;

  function finish() {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }

  async function startServer() {
    setState("starting"); // the mic takes a moment: speak once the button turns red
    const type = pickMimeType((t) => MediaRecorder.isTypeSupported(t));
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      onError(
        "Microphone access was blocked. Allow it in the browser to use voice.",
      );
      setState("idle");
      return;
    }
    const chunks: Blob[] = [];
    const rec = new MediaRecorder(
      stream,
      type ? { mimeType: type } : undefined,
    );
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      finish();
      stream.getTracks().forEach((track) => track.stop());
      recorder.current = null;
      const audio = new Blob(chunks, {
        type: rec.mimeType || type || "audio/webm",
      });
      if (!audio.size) return setState("idle");
      setState("transcribing");
      try {
        onText(await transcribe(audio, lang));
      } catch (err) {
        onError(
          err instanceof ApiError
            ? err.message
            : "Voice input failed. Please type instead.",
        );
      } finally {
        setState("idle");
      }
    };
    rec.onstart = () => setState("listening"); // first words were lost when shown earlier
    recorder.current = rec;
    rec.start();
    timer.current = setTimeout(
      () => rec.state === "recording" && rec.stop(),
      MAX_RECORDING_MS,
    );
  }

  function startBrowser() {
    const Ctor = browserRecognition();
    if (!Ctor) return;
    const rec = new Ctor();
    rec.lang = recognitionLang(recentText, lang);
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = (event) =>
      onText(Array.from(event.results, (r) => r[0].transcript).join(" "));
    rec.onerror = (event) => {
      if (event.error !== "no-speech" && event.error !== "aborted") {
        onError(
          event.error === "not-allowed"
            ? "Microphone access was blocked. Allow it in the browser to use voice."
            : "Voice input failed. Please type instead.",
        );
      }
    };
    rec.onend = () => {
      finish();
      recognition.current = null;
      setState("idle");
    };
    rec.onstart = () => setState("listening");
    recognition.current = rec;
    setState("starting");
    rec.start();
    timer.current = setTimeout(() => rec.stop(), MAX_RECORDING_MS);
  }

  function toggle() {
    if (state === "listening") {
      recorder.current?.stop();
      recognition.current?.stop();
      return;
    }
    if (mode === "server") void startServer();
    else startBrowser();
  }

  const label =
    state === "listening"
      ? "Listening… click to stop"
      : state === "starting"
        ? "Starting the microphone…"
        : state === "transcribing"
          ? "Transcribing"
          : "Speak your question";
  const picked = VOICE_LANGS.find((l) => l.value === lang) ?? VOICE_LANGS[0];
  function switchLang() {
    const next = nextVoiceLang(lang);
    setLang(next);
    try {
      window.localStorage.setItem(LANG_KEY, next);
    } catch {
      // storage blocked: the choice lasts for this page only
    }
  }
  return (
    <div className="flex items-end gap-1">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-10 min-w-11 px-2 text-xs text-muted-foreground"
        onClick={switchLang}
        disabled={state !== "idle"}
        aria-label={`Voice language: ${picked.name}. Click to change.`}
        title={`Voice language: ${picked.name}`}
      >
        {picked.label}
      </Button>
      <Button
        type="button"
        size="icon-lg"
        variant={state === "listening" ? "destructive" : "outline"}
        onClick={toggle}
        disabled={disabled || state === "transcribing" || state === "starting"}
        aria-label={label}
        aria-pressed={state === "listening"}
        title={label}
      >
        {state === "listening" ? (
          <Square aria-hidden />
        ) : state === "transcribing" || state === "starting" ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <Mic aria-hidden />
        )}
      </Button>
    </div>
  );
}

// Push-to-talk (C8.1, DECISION D-055). The transcript is put in the message box for the user to
// check and edit; it is never sent on its own. Server transcription (Deepgram) when the API has
// a key, otherwise the browser's own speech recognition (Chrome / Edge), otherwise no mic.

import { apiFetch, apiJson } from "./api";

export type VoiceMode = "server" | "browser" | "none";
export type VoiceLang = "auto" | "en" | "hi";

export const VOICE_LANGS: { value: VoiceLang; label: string; name: string }[] = [
  { value: "auto", label: "Auto", name: "automatic (English and Hindi)" },
  { value: "en", label: "EN", name: "English" },
  { value: "hi", label: "हिं", name: "Hindi" },
];

export function nextVoiceLang(current: VoiceLang): VoiceLang {
  const i = VOICE_LANGS.findIndex((l) => l.value === current);
  return VOICE_LANGS[(i + 1) % VOICE_LANGS.length].value;
}

export const MAX_RECORDING_MS = 60_000; // a question takes seconds; stop runaway recordings

// Containers MediaRecorder can produce, best first; the API accepts all of them.
const MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];

export function pickMimeType(isSupported: (type: string) => boolean): string | null {
  return MIME_TYPES.find((type) => isSupported(type)) ?? null;
}

/** Browser recognition needs one language: the user's pick, or Hindi after Devanagari text. */
export function recognitionLang(recentText: string, picked: VoiceLang = "auto"): "hi-IN" | "en-IN" {
  if (picked !== "auto") return picked === "hi" ? "hi-IN" : "en-IN";
  return /[ऀ-ॿ]/.test(recentText) ? "hi-IN" : "en-IN";
}

/** Add the transcript to what is already typed, never replacing it. */
export function appendTranscript(draft: string, text: string): string {
  const spoken = text.trim();
  if (!spoken) return draft;
  return draft.trim() ? `${draft.trimEnd()} ${spoken}` : spoken;
}

export function chooseMode(server: boolean, browserRecognition: boolean, mediaRecorder: boolean): VoiceMode {
  if (server && mediaRecorder) return "server";
  return browserRecognition ? "browser" : "none";
}

export async function serverVoiceAvailable(): Promise<boolean> {
  try {
    return (await apiJson<{ server: boolean }>("/voice/status")).server;
  } catch {
    return false;
  }
}

export async function transcribe(audio: Blob, lang: VoiceLang = "auto"): Promise<string> {
  const response = await apiFetch(`/voice/transcribe?lang=${lang}`, {
    method: "POST",
    body: audio,
    headers: { "Content-Type": audio.type.split(";")[0] || "audio/webm" },
  });
  return ((await response.json()) as { text: string }).text;
}

// Minimal typing for the Web Speech API (not in TypeScript's DOM library everywhere).
export type Recognition = {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
  start: () => void;
  stop: () => void;
};

export function browserRecognition(): (new () => Recognition) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  return ((w.SpeechRecognition ?? w.webkitSpeechRecognition) as (new () => Recognition) | undefined) ?? null;
}

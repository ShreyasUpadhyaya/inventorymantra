import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendTranscript,
  chooseMode,
  nextVoiceLang,
  pickMimeType,
  recognitionLang,
  transcribe,
} from "./voice";

describe("voice input helpers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("prefers opus in webm, falls back to what the browser can record", () => {
    expect(pickMimeType(() => true)).toBe("audio/webm;codecs=opus");
    expect(pickMimeType((t) => t === "audio/mp4")).toBe("audio/mp4"); // Safari
    expect(pickMimeType(() => false)).toBeNull();
  });

  it("listens in Hindi after Devanagari, otherwise Indian English", () => {
    expect(recognitionLang("कौन से आइटम खत्म होने वाले हैं?")).toBe("hi-IN");
    expect(recognitionLang("kaun se PO late hain")).toBe("en-IN");
    expect(recognitionLang("")).toBe("en-IN");
    expect(recognitionLang("कौन से", "en")).toBe("en-IN"); // the pick wins
    expect(recognitionLang("", "hi")).toBe("hi-IN");
  });

  it("the language button cycles Auto, EN, Hindi", () => {
    expect(nextVoiceLang("auto")).toBe("en");
    expect(nextVoiceLang("en")).toBe("hi");
    expect(nextVoiceLang("hi")).toBe("auto");
  });

  it("adds the words to the message box instead of replacing or sending", () => {
    expect(appendTranscript("", " Which POs are overdue? ")).toBe("Which POs are overdue?");
    expect(appendTranscript("stock of bush", "in 132-1")).toBe("stock of bush in 132-1");
    expect(appendTranscript("typed", "   ")).toBe("typed");
  });

  it("uses the server when it has a provider, else the browser, else no mic", () => {
    expect(chooseMode(true, true, true)).toBe("server");
    expect(chooseMode(true, true, false)).toBe("browser"); // can't record: browser recognition
    expect(chooseMode(false, true, true)).toBe("browser");
    expect(chooseMode(false, false, true)).toBe("none");
  });

  it("posts the raw audio with its content type and returns the text", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ text: "kaun se PO late hain", provider: "deepgram" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const audio = new Blob(["opus"], { type: "audio/webm;codecs=opus" });
    expect(await transcribe(audio, "hi")).toBe("kaun se PO late hain");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/voice/transcribe?lang=hi");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("audio/webm");
    expect(init.body).toBe(audio);
  });
});

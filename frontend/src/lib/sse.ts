// Minimal Server-Sent Events parser for fetch() streams (EventSource can't POST or send JSON).
// The API sends `event: <kind>` + one `data: <json>` line per event, separated by a blank line.

export type SSEMessage = { event: string; data: string };

export class SSEParser {
  private buffer = "";

  /** Feed a decoded chunk; returns the events completed by it. */
  push(chunk: string): SSEMessage[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const blocks = this.buffer.split("\n\n");
    this.buffer = blocks.pop() ?? ""; // the last part may be incomplete
    return blocks.flatMap((block) => {
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      return data.length ? [{ event, data: data.join("\n") }] : [];
    });
  }
}

/** Read a streamed response body and call onMessage for each event, in order. */
export async function readSSE(
  body: ReadableStream<Uint8Array>,
  onMessage: (message: SSEMessage) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.push(decoder.decode(value, { stream: true })).forEach(onMessage);
  }
  parser.push(decoder.decode() + "\n\n").forEach(onMessage);
}

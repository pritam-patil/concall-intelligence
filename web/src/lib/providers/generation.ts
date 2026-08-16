/**
 * Generation (answer synthesis) provider interface — server-side only.
 *
 * Pinned default: Gemini Flash, free tier. Mirrors
 * ingest/src/ingest/providers/generation.py. Takes the question plus
 * retrieved, source-cited chunks and returns a grounded answer.
 */

export interface GenerationProvider {
  generate(prompt: string, system?: string): Promise<string>;
}

class GeminiFlashGeneration implements GenerationProvider {
  private readonly url: string;
  private readonly apiKey: string;

  constructor(apiKey: string, model: string) {
    this.url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`;
    this.apiKey = apiKey;
  }

  async generate(prompt: string, system?: string): Promise<string> {
    const payload: Record<string, unknown> = {
      contents: [{ role: "user", parts: [{ text: prompt }] }],
    };
    if (system) {
      payload.systemInstruction = { parts: [{ text: system }] };
    }
    const res = await fetch(`${this.url}?key=${this.apiKey}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      throw new Error(`Gemini generation call failed: ${res.status} ${await res.text()}`);
    }
    const data = await res.json();
    return data.candidates[0].content.parts[0].text as string;
  }
}

export function getGenerationProvider(): GenerationProvider {
  const provider = process.env.GENERATION_PROVIDER ?? "gemini_flash";
  if (provider === "gemini_flash") {
    const apiKey = process.env.GEMINI_API_KEY;
    const model = process.env.GEMINI_MODEL ?? "gemini-2.0-flash";
    if (!apiKey) {
      throw new Error("GEMINI_API_KEY is required for GENERATION_PROVIDER=gemini_flash");
    }
    return new GeminiFlashGeneration(apiKey, model);
  }
  throw new Error(`Unknown generation provider: ${provider}`);
}

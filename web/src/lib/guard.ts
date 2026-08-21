import type { NextRequest } from "next/server";
import { getServiceRoleClient } from "./supabase";

/**
 * Abuse guardrails for the public query endpoints: an input length cap, a
 * prompt-injection rejection, and a per-IP daily request cap (Supabase table +
 * an atomic RPC — see supabase/migrations/*_rate_limits.sql).
 *
 * All of these run BEFORE any embedding or LLM call, so a rejected or
 * over-limit request costs at most one cheap RPC, never a paid provider call.
 */

export const MAX_QUESTION_CHARS = Number(process.env.ASK_MAX_QUESTION_CHARS ?? 1000);
export const RATE_LIMIT_PER_DAY = Number(process.env.ASK_RATE_LIMIT_PER_DAY ?? 25);

// Instruction-override / prompt-injection shapes. Kept deliberately specific
// (an override VERB next to "instructions/prompt/rules", persona resets, prompt
// exfiltration) so ordinary finance questions — which may contain words like
// "ignore", "act", "system" in isolation — don't trip them.
const INJECTION_PATTERNS: RegExp[] = [
  /ignore\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|preceding|foregoing)\s+(?:instruction|prompt|rule|message|direction)/i,
  /disregard\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|system)\s+(?:instruction|prompt|rule)/i,
  /forget\s+(?:all\s+|everything\s+|your\s+|the\s+)?(?:previous\s+|prior\s+|above\s+)?(?:instruction|rule|system\s+prompt)/i,
  /(?:reveal|show|print|repeat|expose|leak|display)\s+(?:me\s+)?(?:your\s+|the\s+)?(?:full\s+)?(?:system\s+)?(?:prompt|instruction)/i,
  /you\s+are\s+now\s+(?:a|an|the|no longer|going to)/i,
  /pretend\s+(?:to\s+be|you\s+are|that\s+you)/i,
  /\bnew\s+(?:system\s+)?(?:instruction|prompt|rule)s?\s*[:\-]/i,
  /\b(?:do anything now|dan mode|jailbreak|developer mode)\b/i,
  /(?:override|bypass|disable|turn off)\s+(?:your\s+|the\s+|all\s+)?(?:instruction|rule|system|safety|guardrail|restriction|filter)/i,
];

export function looksLikeInjection(text: string): boolean {
  return INJECTION_PATTERNS.some((re) => re.test(text));
}

export type InputRejection = { status: number; error: string };

/**
 * Validate a user question against the length cap and injection filter. Returns
 * a rejection (HTTP status + message) or null when the input is acceptable.
 */
export function validateQuestion(question: string): InputRejection | null {
  if (question.length > MAX_QUESTION_CHARS) {
    return {
      status: 413,
      error: `Your question is too long (${question.length} characters). Please keep it under ${MAX_QUESTION_CHARS} characters.`,
    };
  }
  if (looksLikeInjection(question)) {
    return {
      status: 400,
      error:
        "That looks like an attempt to change how the assistant works rather than a question about the filings. Please ask a factual question about a covered company's filings or earnings call.",
    };
  }
  return null;
}

/** Best-effort client IP from proxy headers (x-forwarded-for wins on Vercel). */
export function clientIp(req: NextRequest): string {
  const xff = req.headers.get("x-forwarded-for");
  if (xff) return xff.split(",")[0].trim();
  const real = req.headers.get("x-real-ip");
  if (real) return real.trim();
  return "unknown";
}

export type RateLimit = { allowed: boolean; used: number; limit: number };

/**
 * Record one request for `ip` today and report whether it's within the daily
 * cap. Atomic via the rate_limit_hit RPC. FAILS OPEN: if the limiter table/RPC
 * is unavailable (e.g. before the migration is applied, or a transient DB
 * error), the request is allowed rather than the endpoint going down — logged
 * so the gap is visible.
 */
export async function checkRateLimit(ip: string): Promise<RateLimit> {
  const day = new Date().toISOString().slice(0, 10); // UTC YYYY-MM-DD
  try {
    const { data, error } = await getServiceRoleClient().rpc("rate_limit_hit", {
      p_ip: ip,
      p_day: day,
      p_limit: RATE_LIMIT_PER_DAY,
    });
    if (error) throw new Error(error.message);
    const row = Array.isArray(data) ? data[0] : data;
    return {
      allowed: row?.allowed !== false,
      used: Number(row?.used ?? 0),
      limit: RATE_LIMIT_PER_DAY,
    };
  } catch (err) {
    console.warn(
      `[guard] rate-limit check failed, allowing request: ${err instanceof Error ? err.message : String(err)}`,
    );
    return { allowed: true, used: 0, limit: RATE_LIMIT_PER_DAY };
  }
}

/** The clear message shown when the daily cap is hit. */
export function rateLimitMessage(limit: number): string {
  return `Daily limit reached — you've used all ${limit} questions for today. Please try again tomorrow.`;
}

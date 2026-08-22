/**
 * Structured logging: one JSON object per line.
 *
 * Why JSON lines and not Sentry: the app runs on Vercel's free tier, where
 * Runtime Logs already capture stdout/stderr per invocation and parse a
 * JSON line into searchable fields (filter by `event`, `request_id`,
 * `symbol`, …) — no SDK, no DSN, no extra vendor account. Every log call
 * here carries an `event` name (dot-namespaced, e.g. `ask.complete`), so
 * an operator can grep/filter on the event rather than on prose. If a
 * hosted error tracker is ever wanted, `onRequestError` in
 * src/instrumentation.ts is the single seam to forward from.
 *
 * Levels map onto console methods so Vercel classifies them correctly:
 * `error` → console.error, `warn` → console.warn, the rest → console.log.
 * LOG_LEVEL (default "info") silences anything below it. A `err` field is
 * serialised to {name, message, stack, cause?} — pass the caught value
 * itself, never a pre-stringified message, so the stack survives.
 *
 * Runtime-agnostic on purpose (no node: imports): instrumentation.ts
 * imports this and runs in both the Node.js and Edge runtimes.
 */

export type LogLevel = "debug" | "info" | "warn" | "error";
export type LogFields = Record<string, unknown>;

const LEVELS: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };

function minLevel(): number {
  const configured = process.env.LOG_LEVEL as LogLevel | undefined;
  return configured && configured in LEVELS ? LEVELS[configured] : LEVELS.info;
}

// Provider errors embed the upstream response body in the message (Gemini's
// 429 is ~1.5 KB of JSON, repeated inside `stack`). Cap both so one error
// can't bloat a log line past what a log viewer shows, and keep `stack` to
// the frames only — the message is already its own field.
const MAX_MESSAGE_CHARS = 600;
const MAX_STACK_CHARS = 1500;

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}… [+${text.length - max} chars]` : text;
}

function serializeError(err: unknown): LogFields {
  if (err instanceof Error) {
    const out: LogFields = { name: err.name, message: truncate(err.message, MAX_MESSAGE_CHARS) };
    if (err.stack) {
      const header = `${err.name}: ${err.message}`;
      const frames = err.stack.startsWith(header) ? err.stack.slice(header.length).trimStart() : err.stack;
      out.stack = truncate(frames, MAX_STACK_CHARS);
    }
    if (err.cause !== undefined) out.cause = serializeError(err.cause);
    return out;
  }
  return { message: truncate(String(err), MAX_MESSAGE_CHARS) };
}

export function log(level: LogLevel, event: string, fields: LogFields = {}): void {
  if (LEVELS[level] < minLevel()) return;
  const { err, ...rest } = fields;
  const record: LogFields = { ts: new Date().toISOString(), level, event, ...rest };
  if (err !== undefined) record.err = serializeError(err);
  let line: string;
  try {
    line = JSON.stringify(record);
  } catch {
    // A circular field shouldn't take the log line down with it.
    line = JSON.stringify({ ts: record.ts, level, event, unserializable: true });
  }
  if (level === "error") console.error(line);
  else if (level === "warn") console.warn(line);
  else console.log(line);
}

export type Logger = {
  debug(event: string, fields?: LogFields): void;
  info(event: string, fields?: LogFields): void;
  warn(event: string, fields?: LogFields): void;
  error(event: string, fields?: LogFields): void;
  /** A logger that stamps `fields` onto every line (request id, route, …). */
  child(fields: LogFields): Logger;
};

export function createLogger(base: LogFields = {}): Logger {
  const emit = (level: LogLevel) => (event: string, fields: LogFields = {}) =>
    log(level, event, { ...base, ...fields });
  return {
    debug: emit("debug"),
    info: emit("info"),
    warn: emit("warn"),
    error: emit("error"),
    child: (fields) => createLogger({ ...base, ...fields }),
  };
}

export const logger = createLogger();

/**
 * A per-request correlation id. Vercel stamps every request with
 * `x-vercel-id` (which also appears in its Runtime Logs, so a log line can
 * be matched to the platform's own record of the invocation); anywhere
 * else, a fresh UUID.
 */
export function requestId(req: Request): string {
  return req.headers.get("x-vercel-id") ?? crypto.randomUUID();
}

/** `performance.now()`-based stopwatch returning whole milliseconds. */
export function stopwatch(): () => number {
  const start = performance.now();
  return () => Math.round(performance.now() - start);
}

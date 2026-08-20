export type ErrorCode =
  | "config"
  | "timeout"
  | "rate_limited"
  | "upstream"
  | "malformed"
  | "invalid_request"
  | "breaker_open"
  | "disabled";

export class AnswerError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly status = 500,
    readonly retryAfter?: number,
  ) {
    super(message);
    this.name = "AnswerError";
  }
}

/** Missing/invalid configuration. Never carries the offending value. */
export class ConfigError extends AnswerError {
  constructor(varName: string) {
    super("config", `Missing required configuration: ${varName}`, 503);
  }
}

export class TimeoutError extends AnswerError {
  constructor(ms: number) {
    super("timeout", `Upstream did not respond within ${ms}ms`, 504);
  }
}

export class RateLimitedError extends AnswerError {
  constructor(retryAfter: number) {
    super("rate_limited", "Too many requests", 429, retryAfter);
  }
}

export class UpstreamError extends AnswerError {
  constructor(readonly httpStatus: number, retryAfter?: number) {
    super("upstream", `Upstream returned ${httpStatus}`, 502, retryAfter);
  }
  /** 4xx (except 429) are caller errors and must never be retried. */
  get retryable(): boolean {
    return this.httpStatus === 429 || this.httpStatus >= 500;
  }
}

export class MalformedResponseError extends AnswerError {
  constructor(detail: string) {
    super("malformed", `Malformed upstream response: ${detail}`, 502);
  }
}

export class BreakerOpenError extends AnswerError {
  constructor() {
    super("breaker_open", "Answer service temporarily unavailable", 503, 60);
  }
}

/**
 * Maps any thrown value to a safe client payload. Internal messages, stack
 * traces and configuration details are deliberately dropped.
 */
export function toClientError(err: unknown): {
  code: ErrorCode;
  status: number;
  message: string;
  retryAfter?: number;
} {
  const safe: Record<ErrorCode, string> = {
    config: "The answer service is not configured yet.",
    timeout: "That took too long. Please try again.",
    rate_limited: "You've asked a lot of questions — take a breather and try again shortly.",
    upstream: "The answer service had a hiccup. Please try again.",
    malformed: "The answer service returned something unexpected.",
    invalid_request: "That question couldn't be read.",
    breaker_open: "The answer service is recovering. Please try again in a minute.",
    disabled: "Not found.",
  };
  if (err instanceof AnswerError) {
    return {
      code: err.code,
      status: err.status,
      message: safe[err.code],
      retryAfter: err.retryAfter,
    };
  }
  return { code: "upstream", status: 502, message: safe.upstream };
}

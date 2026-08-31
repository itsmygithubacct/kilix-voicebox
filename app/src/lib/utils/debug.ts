const DEBUG = import.meta.env.DEV;

function safeArguments(args: unknown[]): unknown[] {
  return args.map((value, index) => {
    if (index === 0 && typeof value === 'string') {
      return value.replace(/([?&]session=)[^&\s]+/g, '$1[redacted]');
    }
    if (typeof value === 'number' || typeof value === 'boolean' || value == null) {
      return value;
    }
    return '[redacted]';
  });
}

export const debug = {
  log: (...args: unknown[]) => {
    if (DEBUG) {
      console.log(...safeArguments(args));
    }
  },
  error: (...args: unknown[]) => {
    if (DEBUG) {
      console.error(...safeArguments(args));
    }
  },
  warn: (...args: unknown[]) => {
    if (DEBUG) {
      console.warn(...safeArguments(args));
    }
  },
};

import type { PlatformLifecycle, ServerSession } from '@/platform/types';

class WebLifecycle implements PlatformLifecycle {
  onServerReady?: () => void;

  async startServer(): Promise<ServerSession> {
    const token = import.meta.env.VITE_VOICEBOX_SESSION_TOKEN ?? '';
    if (!/^[0-9a-f]{64}$/.test(token)) {
      throw new Error('VOICEBOX_SESSION_INVALID');
    }
    this.onServerReady?.();
    return { url: 'http://127.0.0.1:17493', token };
  }

  async stopServer(): Promise<void> {
    // No-op for web - server is managed externally
  }
}

export const webLifecycle = new WebLifecycle();

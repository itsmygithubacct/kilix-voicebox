import type { PlatformLifecycle, ServerSession } from '@/platform/types';

class WebLifecycle implements PlatformLifecycle {
  onServerReady?: () => void;

  async startServer(): Promise<ServerSession> {
    const installed = window.location.pathname.startsWith('/ui/');
    const token = installed
      ? new URLSearchParams(window.location.hash.slice(1)).get('session') ?? ''
      : import.meta.env.VITE_VOICEBOX_SESSION_TOKEN ?? '';
    if (!/^[0-9a-f]{64}$/.test(token)) {
      throw new Error('VOICEBOX_SESSION_INVALID');
    }
    if (installed) window.history.replaceState(null, '', window.location.pathname);
    this.onServerReady?.();
    return { url: installed ? window.location.origin : 'http://127.0.0.1:17493', token };
  }

  async stopServer(): Promise<void> {
    // No-op for web - server is managed externally
  }
}

export const webLifecycle = new WebLifecycle();

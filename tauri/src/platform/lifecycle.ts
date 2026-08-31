import { invoke } from '@tauri-apps/api/core';
import type { PlatformLifecycle, ServerSession } from '@/platform/types';

class TauriLifecycle implements PlatformLifecycle {
  onServerReady?: () => void;

  async startServer(): Promise<ServerSession> {
    try {
      return await invoke<ServerSession>('start_server');
    } catch {
      throw new Error('voicebox.server_start_failed');
    }
  }

  async stopServer(): Promise<void> {
    try {
      await invoke('stop_server');
    } catch {
      throw new Error('voicebox.server_stop_failed');
    }
  }
}

export const tauriLifecycle = new TauriLifecycle();

import { create } from 'zustand';
import type { ServerSession } from '@/platform/types';

export const LOCAL_SERVER_URL = 'http://127.0.0.1:17493';

interface ServerStore {
  serverUrl: string;
  sessionToken: string;
  setServerSession: (session: ServerSession) => void;

  isConnected: boolean;
  setIsConnected: (connected: boolean) => void;

  mode: 'local';
  setMode: (mode: 'local') => void;
}

export const useServerStore = create<ServerStore>()((set) => ({
  serverUrl: LOCAL_SERVER_URL,
  sessionToken: import.meta.env.VITE_VOICEBOX_SESSION_TOKEN ?? '',
  setServerSession: (session) => {
    const installed = window.location.pathname.startsWith('/ui/');
    const expected = installed ? window.location.origin : LOCAL_SERVER_URL;
    if (session.url !== expected || !/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(expected)
        || !/^[0-9a-f]{64}$/.test(session.token)) {
      throw new Error('VOICEBOX_SESSION_INVALID');
    }
    set({ serverUrl: expected, sessionToken: session.token });
  },

  isConnected: false,
  setIsConnected: (connected) => set({ isConnected: connected }),

  mode: 'local',
  setMode: (mode) => set({ mode }),
}));

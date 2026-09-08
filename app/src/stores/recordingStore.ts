import { create } from 'zustand';

export interface RecordingStatus {
  phase: 'inactive' | 'starting' | 'recording' | 'stopping' | 'unavailable';
  id: string | null;
  last_id: string | null;
  duration: number;
}

// An aborted request is not cleanup proof. This survives closing sample dialogs.
export const useRecordingStore = create<{
  pendingId: string | null;
  status: RecordingStatus;
  begin: (id: string) => void;
  refresh: (status: RecordingStatus) => void;
  uncertain: () => void;
}>((set) => ({
  pendingId: null,
  status: { phase: 'inactive', id: null, last_id: null, duration: 0 },
  begin: (id) => set({ pendingId: id, status: {
    phase: 'starting', id, last_id: null, duration: 0,
  } }),
  refresh: (status) => set((state) => {
    if (state.pendingId && status.phase === 'inactive' && status.last_id !== state.pendingId) {
      return {};
    }
    return { status, pendingId: status.last_id === state.pendingId ? null : state.pendingId };
  }),
  uncertain: () => set((state) => ({ status: { ...state.status, phase: 'unavailable' } })),
}));

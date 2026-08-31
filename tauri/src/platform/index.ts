import type { Platform } from '@/platform/types';
import { tauriAudio } from './audio';
import { tauriFilesystem } from './filesystem';
import { tauriLifecycle } from './lifecycle';
import { tauriMetadata } from './metadata';

export const tauriPlatform: Platform = {
  filesystem: tauriFilesystem,
  audio: tauriAudio,
  lifecycle: tauriLifecycle,
  metadata: tauriMetadata,
};

import type { Platform } from '@/platform/types';
import { webAudio } from './audio';
import { webFilesystem } from './filesystem';
import { webLifecycle } from './lifecycle';
import { webMetadata } from './metadata';

export const webPlatform: Platform = {
  filesystem: webFilesystem,
  audio: webAudio,
  lifecycle: webLifecycle,
  metadata: webMetadata,
};

import type { LanguageCode } from '@/lib/constants/languages';
import { useServerStore } from '@/stores/serverStore';
import type { RecordingStatus } from '@/stores/recordingStore';
import type {
  DeletePreview,
  DeleteReceipt,
  GenerationRequest,
  GenerationResponse,
  HealthResponse,
  HistoryListResponse,
  HistoryQuery,
  HistoryResponse,
  ModelStatusListResponse,
  ProfileSampleResponse,
  StoryCreate,
  StoryDetailResponse,
  StoryItemBatchUpdate,
  StoryItemCreate,
  StoryItemDetail,
  StoryItemMove,
  StoryItemReorder,
  StoryItemSplit,
  StoryItemTrim,
  StoryResponse,
  TranscriptionResponse,
  VoiceProfileCreate,
  VoiceProfileResponse,
} from './types';

class ApiClient {
  private getBaseUrl(): string {
    const serverUrl = useServerStore.getState().serverUrl;
    return serverUrl;
  }

  private getSessionToken(): string {
    return useServerStore.getState().sessionToken;
  }

  private authenticatedFetch(url: string, options?: RequestInit): Promise<Response> {
    const headers = new Headers(options?.headers);
    headers.set('X-Kilix-Voicebox-Session', this.getSessionToken());
    return fetch(url, { ...options, headers });
  }

  private mediaUrl(endpoint: string): string {
    const token = encodeURIComponent(this.getSessionToken());
    return `${this.getBaseUrl()}${endpoint}?session=${token}`;
  }

  private async throwResponseError(response: Response): Promise<never> {
    const error = await response.json().catch(() => null);
    const detail =
      typeof error === 'object' && error !== null && 'detail' in error ? error.detail : null;
    const candidate =
      typeof detail === 'object' && detail !== null && 'code' in detail ? detail.code : detail;
    const code =
      typeof candidate === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(candidate)
        ? candidate
        : `HTTP_${response.status}`;
    throw new Error(code);
  }

  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const url = `${this.getBaseUrl()}${endpoint}`;
    const response = await this.authenticatedFetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.json();
  }

  // Health
  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health');
  }

  // Profiles
  async createProfile(data: VoiceProfileCreate): Promise<VoiceProfileResponse> {
    return this.request<VoiceProfileResponse>('/profiles', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async listProfiles(): Promise<VoiceProfileResponse[]> {
    return this.request<VoiceProfileResponse[]>('/profiles');
  }

  async getProfile(profileId: string): Promise<VoiceProfileResponse> {
    return this.request<VoiceProfileResponse>(`/profiles/${profileId}`);
  }

  async updateProfile(profileId: string, data: VoiceProfileCreate): Promise<VoiceProfileResponse> {
    return this.request<VoiceProfileResponse>(`/profiles/${profileId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async getProfileDeletePreview(profileId: string): Promise<DeletePreview> {
    return this.request<DeletePreview>(`/profiles/${encodeURIComponent(profileId)}/delete-preview`);
  }

  async deleteProfile(profileId: string): Promise<DeleteReceipt> {
    return this.request<DeleteReceipt>(`/profiles/${encodeURIComponent(profileId)}`, {
      method: 'DELETE',
    });
  }

  async addProfileSample(
    profileId: string,
    file: File,
    referenceText: string,
  ): Promise<ProfileSampleResponse> {
    const url = `${this.getBaseUrl()}/profiles/${profileId}/samples`;
    const formData = new FormData();
    formData.append('file', file);
    formData.append('reference_text', referenceText);

    const response = await this.authenticatedFetch(url, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.json();
  }

  async listProfileSamples(profileId: string): Promise<ProfileSampleResponse[]> {
    return this.request<ProfileSampleResponse[]>(`/profiles/${profileId}/samples`);
  }

  async getProfileSampleDeletePreview(sampleId: string): Promise<DeletePreview> {
    return this.request<DeletePreview>(
      `/profiles/samples/${encodeURIComponent(sampleId)}/delete-preview`,
    );
  }

  async deleteProfileSample(sampleId: string): Promise<DeleteReceipt> {
    return this.request<DeleteReceipt>(`/profiles/samples/${encodeURIComponent(sampleId)}`, {
      method: 'DELETE',
    });
  }

  async updateProfileSample(
    sampleId: string,
    referenceText: string,
  ): Promise<ProfileSampleResponse> {
    return this.request<ProfileSampleResponse>(`/profiles/samples/${sampleId}`, {
      method: 'PUT',
      body: JSON.stringify({ reference_text: referenceText }),
    });
  }

  async exportProfile(profileId: string, includeSamples = false): Promise<Blob> {
    const query = includeSamples ? '?include_samples=true' : '';
    const url = `${this.getBaseUrl()}/profiles/${profileId}/export${query}`;
    const response = await this.authenticatedFetch(url);

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.blob();
  }

  async importProfile(file: File): Promise<VoiceProfileResponse> {
    const url = `${this.getBaseUrl()}/profiles/import`;
    const formData = new FormData();
    formData.append('file', file);

    const response = await this.authenticatedFetch(url, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.json();
  }

  async uploadAvatar(profileId: string, file: File): Promise<VoiceProfileResponse> {
    const url = `${this.getBaseUrl()}/profiles/${profileId}/avatar`;
    const formData = new FormData();
    formData.append('file', file);

    const response = await this.authenticatedFetch(url, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.json();
  }

  async getAvatarDeletePreview(profileId: string): Promise<DeletePreview> {
    return this.request<DeletePreview>(
      `/profiles/${encodeURIComponent(profileId)}/avatar/delete-preview`,
    );
  }

  async deleteAvatar(profileId: string): Promise<DeleteReceipt> {
    return this.request<DeleteReceipt>(`/profiles/${encodeURIComponent(profileId)}/avatar`, {
      method: 'DELETE',
    });
  }

  // Generation
  async generateSpeech(data: GenerationRequest): Promise<GenerationResponse> {
    return this.request<GenerationResponse>('/generate', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  // History
  async listHistory(query?: HistoryQuery): Promise<HistoryListResponse> {
    const params = new URLSearchParams();
    if (query?.profile_id) params.append('profile_id', query.profile_id);
    if (query?.search) params.append('search', query.search);
    if (query?.limit) params.append('limit', query.limit.toString());
    if (query?.offset) params.append('offset', query.offset.toString());

    const queryString = params.toString();
    const endpoint = queryString ? `/history?${queryString}` : '/history';

    return this.request<HistoryListResponse>(endpoint);
  }

  async getGeneration(generationId: string): Promise<HistoryResponse> {
    return this.request<HistoryResponse>(`/history/${generationId}`);
  }

  async getGenerationDeletePreview(generationId: string): Promise<DeletePreview> {
    return this.request<DeletePreview>(
      `/history/${encodeURIComponent(generationId)}/delete-preview`,
    );
  }

  async deleteGeneration(generationId: string): Promise<DeleteReceipt> {
    return this.request<DeleteReceipt>(`/history/${encodeURIComponent(generationId)}`, {
      method: 'DELETE',
    });
  }

  async exportGeneration(generationId: string): Promise<Blob> {
    const url = `${this.getBaseUrl()}/history/${generationId}/export`;
    const response = await this.authenticatedFetch(url);

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.blob();
  }

  async exportGenerationAudio(generationId: string): Promise<Blob> {
    const url = `${this.getBaseUrl()}/history/${generationId}/export-audio`;
    const response = await this.authenticatedFetch(url);

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.blob();
  }

  // Audio
  getAudioUrl(audioId: string): string {
    return this.mediaUrl(`/audio/${encodeURIComponent(audioId)}`);
  }

  getSampleUrl(sampleId: string): string {
    return this.mediaUrl(`/samples/${encodeURIComponent(sampleId)}`);
  }

  getAvatarUrl(profileId: string): string {
    return this.mediaUrl(`/profiles/${encodeURIComponent(profileId)}/avatar`);
  }

  // Transcription
  async recordingStatus(signal: AbortSignal): Promise<RecordingStatus> {
    return this.request<RecordingStatus>('/recording/status', {
      cache: 'no-store',
      signal: AbortSignal.any([signal, AbortSignal.timeout(1000)]),
    });
  }

  async controlRecording(id: string, operation: 'stop' | 'cancel'): Promise<RecordingStatus> {
    return this.request<RecordingStatus>(`/recording/${id}/${operation}`, {
      method: 'POST', signal: AbortSignal.timeout(1000),
    });
  }

  async recordAudio(id: string, seconds: number, signal: AbortSignal) {
    const response = await this.authenticatedFetch(`${this.getBaseUrl()}/recording`, {
      method: 'POST', signal: AbortSignal.any([signal, AbortSignal.timeout((seconds + 6) * 1000)]),
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, seconds }),
    });
    if (!response.ok) await this.throwResponseError(response);
    const blob = await response.blob();
    const duration = Number(response.headers.get('X-Kilix-Recording-Duration'));
    if (blob.type !== 'audio/wav' || blob.size > 960044 ||
        !Number.isFinite(duration) || duration <= 0 || duration > 30) {
      throw new Error('RECORDING_INVALID');
    }
    return { blob, duration };
  }

  async transcribeAudio(file: File, language?: LanguageCode, signal?: AbortSignal): Promise<TranscriptionResponse> {
    const formData = new FormData();
    formData.append('file', file);
    if (language) {
      formData.append('language', language);
    }

    const url = `${this.getBaseUrl()}/transcribe`;
    const response = await this.authenticatedFetch(url, {
      method: 'POST',
      body: formData,
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(300000)]) : AbortSignal.timeout(300000),
    });

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.json();
  }

  // Model Management
  async getModelStatus(): Promise<ModelStatusListResponse> {
    return this.request<ModelStatusListResponse>('/models/status');
  }

  // Audio Channels
  async listChannels(): Promise<
    Array<{
      id: string;
      name: string;
      is_default: boolean;
      device_ids: string[];
      created_at: string;
    }>
  > {
    return this.request('/channels');
  }

  async createChannel(data: { name: string; device_ids: string[] }): Promise<{
    id: string;
    name: string;
    is_default: boolean;
    device_ids: string[];
    created_at: string;
  }> {
    return this.request('/channels', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async updateChannel(
    channelId: string,
    data: {
      name?: string;
      device_ids?: string[];
    },
  ): Promise<{
    id: string;
    name: string;
    is_default: boolean;
    device_ids: string[];
    created_at: string;
  }> {
    return this.request(`/channels/${channelId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async deleteChannel(channelId: string): Promise<{ message: string }> {
    return this.request(`/channels/${channelId}`, {
      method: 'DELETE',
    });
  }

  async getChannelVoices(channelId: string): Promise<{ profile_ids: string[] }> {
    return this.request(`/channels/${channelId}/voices`);
  }

  async setChannelVoices(channelId: string, profileIds: string[]): Promise<{ message: string }> {
    return this.request(`/channels/${channelId}/voices`, {
      method: 'PUT',
      body: JSON.stringify({ profile_ids: profileIds }),
    });
  }

  async getProfileChannels(profileId: string): Promise<{ channel_ids: string[] }> {
    return this.request(`/profiles/${profileId}/channels`);
  }

  async setProfileChannels(profileId: string, channelIds: string[]): Promise<{ message: string }> {
    return this.request(`/profiles/${profileId}/channels`, {
      method: 'PUT',
      body: JSON.stringify({ channel_ids: channelIds }),
    });
  }

  // Stories
  async listStories(): Promise<StoryResponse[]> {
    return this.request<StoryResponse[]>('/stories');
  }

  async createStory(data: StoryCreate): Promise<StoryResponse> {
    return this.request<StoryResponse>('/stories', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async getStory(storyId: string): Promise<StoryDetailResponse> {
    return this.request<StoryDetailResponse>(`/stories/${storyId}`);
  }

  async updateStory(storyId: string, data: StoryCreate): Promise<StoryResponse> {
    return this.request<StoryResponse>(`/stories/${storyId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async getStoryDeletePreview(storyId: string): Promise<DeletePreview> {
    return this.request<DeletePreview>(`/stories/${encodeURIComponent(storyId)}/delete-preview`);
  }

  async deleteStory(storyId: string): Promise<DeleteReceipt> {
    return this.request<DeleteReceipt>(`/stories/${encodeURIComponent(storyId)}`, {
      method: 'DELETE',
    });
  }

  async addStoryItem(storyId: string, data: StoryItemCreate): Promise<StoryItemDetail> {
    return this.request<StoryItemDetail>(`/stories/${storyId}/items`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async removeStoryItem(storyId: string, itemId: string): Promise<void> {
    await this.request<void>(`/stories/${storyId}/items/${itemId}`, {
      method: 'DELETE',
    });
  }

  async updateStoryItemTimes(storyId: string, data: StoryItemBatchUpdate): Promise<void> {
    await this.request<void>(`/stories/${storyId}/items/times`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async reorderStoryItems(storyId: string, data: StoryItemReorder): Promise<StoryItemDetail[]> {
    return this.request<StoryItemDetail[]>(`/stories/${storyId}/items/reorder`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async moveStoryItem(
    storyId: string,
    itemId: string,
    data: StoryItemMove,
  ): Promise<StoryItemDetail> {
    return this.request<StoryItemDetail>(`/stories/${storyId}/items/${itemId}/move`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async trimStoryItem(
    storyId: string,
    itemId: string,
    data: StoryItemTrim,
  ): Promise<StoryItemDetail> {
    return this.request<StoryItemDetail>(`/stories/${storyId}/items/${itemId}/trim`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async splitStoryItem(
    storyId: string,
    itemId: string,
    data: StoryItemSplit,
  ): Promise<StoryItemDetail[]> {
    return this.request<StoryItemDetail[]>(`/stories/${storyId}/items/${itemId}/split`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async duplicateStoryItem(storyId: string, itemId: string): Promise<StoryItemDetail> {
    return this.request<StoryItemDetail>(`/stories/${storyId}/items/${itemId}/duplicate`, {
      method: 'POST',
    });
  }

  async exportStoryAudio(storyId: string): Promise<Blob> {
    const url = `${this.getBaseUrl()}/stories/${storyId}/export-audio`;
    const response = await this.authenticatedFetch(url);

    if (!response.ok) {
      await this.throwResponseError(response);
    }

    return response.blob();
  }
}

export const apiClient = new ApiClient();

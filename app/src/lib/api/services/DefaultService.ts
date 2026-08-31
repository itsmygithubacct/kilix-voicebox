/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AudioChannelCreate } from '../models/AudioChannelCreate';
import type { AudioChannelResponse } from '../models/AudioChannelResponse';
import type { AudioChannelUpdate } from '../models/AudioChannelUpdate';
import type { Body_add_profile_sample_profiles__profile_id__samples_post } from '../models/Body_add_profile_sample_profiles__profile_id__samples_post';
import type { Body_import_profile_profiles_import_post } from '../models/Body_import_profile_profiles_import_post';
import type { Body_transcribe_audio_transcribe_post } from '../models/Body_transcribe_audio_transcribe_post';
import type { Body_upload_profile_avatar_profiles__profile_id__avatar_post } from '../models/Body_upload_profile_avatar_profiles__profile_id__avatar_post';
import type { ChannelVoiceAssignment } from '../models/ChannelVoiceAssignment';
import type { GenerationRequest } from '../models/GenerationRequest';
import type { GenerationResponse } from '../models/GenerationResponse';
import type { HealthResponse } from '../models/HealthResponse';
import type { HistoryListResponse } from '../models/HistoryListResponse';
import type { HistoryResponse } from '../models/HistoryResponse';
import type { ModelStatusListResponse } from '../models/ModelStatusListResponse';
import type { ProfileChannelAssignment } from '../models/ProfileChannelAssignment';
import type { ProfileSampleResponse } from '../models/ProfileSampleResponse';
import type { ProfileSampleUpdate } from '../models/ProfileSampleUpdate';
import type { StoryCreate } from '../models/StoryCreate';
import type { StoryDetailResponse } from '../models/StoryDetailResponse';
import type { StoryItemBatchUpdate } from '../models/StoryItemBatchUpdate';
import type { StoryItemCreate } from '../models/StoryItemCreate';
import type { StoryItemDetail } from '../models/StoryItemDetail';
import type { StoryItemMove } from '../models/StoryItemMove';
import type { StoryItemReorder } from '../models/StoryItemReorder';
import type { StoryItemSplit } from '../models/StoryItemSplit';
import type { StoryItemTrim } from '../models/StoryItemTrim';
import type { StoryResponse } from '../models/StoryResponse';
import type { TranscriptionResponse } from '../models/TranscriptionResponse';
import type { VoiceProfileCreate } from '../models/VoiceProfileCreate';
import type { VoiceProfileResponse } from '../models/VoiceProfileResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DefaultService {
    /**
     * Root
     * Root endpoint.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static rootGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/',
        });
    }
    /**
     * Health
     * Report provider reachability without importing or inspecting model runtimes.
     * @returns HealthResponse Successful Response
     * @throws ApiError
     */
    public static healthHealthGet(): CancelablePromise<HealthResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/health',
        });
    }
    /**
     * List Profiles
     * List all voice profiles.
     * @returns VoiceProfileResponse Successful Response
     * @throws ApiError
     */
    public static listProfilesProfilesGet(): CancelablePromise<Array<VoiceProfileResponse>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles',
        });
    }
    /**
     * Create Profile
     * Create a new voice profile.
     * @returns VoiceProfileResponse Successful Response
     * @throws ApiError
     */
    public static createProfileProfilesPost({
        requestBody,
    }: {
        requestBody: VoiceProfileCreate,
    }): CancelablePromise<VoiceProfileResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/profiles',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Import Profile
     * Import a voice profile from a ZIP archive.
     * @returns VoiceProfileResponse Successful Response
     * @throws ApiError
     */
    public static importProfileProfilesImportPost({
        formData,
    }: {
        formData: Body_import_profile_profiles_import_post,
    }): CancelablePromise<VoiceProfileResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/profiles/import',
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Profile
     * Get a voice profile by ID.
     * @returns VoiceProfileResponse Successful Response
     * @throws ApiError
     */
    public static getProfileProfilesProfileIdGet({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<VoiceProfileResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles/{profile_id}',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Profile
     * Update a voice profile.
     * @returns VoiceProfileResponse Successful Response
     * @throws ApiError
     */
    public static updateProfileProfilesProfileIdPut({
        profileId,
        requestBody,
    }: {
        profileId: string,
        requestBody: VoiceProfileCreate,
    }): CancelablePromise<VoiceProfileResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/profiles/{profile_id}',
            path: {
                'profile_id': profileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Profile
     * Move a profile to recoverable trash after its preview is available.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static deleteProfileProfilesProfileIdDelete({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/profiles/{profile_id}',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preview Profile Delete
     * Preview the exact profile population that delete moves to trash.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static previewProfileDeleteProfilesProfileIdDeletePreviewGet({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles/{profile_id}/delete-preview',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Profile Sample
     * Add a sample to a voice profile.
     * @returns ProfileSampleResponse Successful Response
     * @throws ApiError
     */
    public static addProfileSampleProfilesProfileIdSamplesPost({
        profileId,
        formData,
    }: {
        profileId: string,
        formData: Body_add_profile_sample_profiles__profile_id__samples_post,
    }): CancelablePromise<ProfileSampleResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/profiles/{profile_id}/samples',
            path: {
                'profile_id': profileId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Profile Samples
     * Get all samples for a profile.
     * @returns ProfileSampleResponse Successful Response
     * @throws ApiError
     */
    public static getProfileSamplesProfilesProfileIdSamplesGet({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<Array<ProfileSampleResponse>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles/{profile_id}/samples',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Profile Sample
     * Move a profile sample to recoverable trash.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static deleteProfileSampleProfilesSamplesSampleIdDelete({
        sampleId,
    }: {
        sampleId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/profiles/samples/{sample_id}',
            path: {
                'sample_id': sampleId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Profile Sample
     * Update a profile sample's reference text.
     * @returns ProfileSampleResponse Successful Response
     * @throws ApiError
     */
    public static updateProfileSampleProfilesSamplesSampleIdPut({
        sampleId,
        requestBody,
    }: {
        sampleId: string,
        requestBody: ProfileSampleUpdate,
    }): CancelablePromise<ProfileSampleResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/profiles/samples/{sample_id}',
            path: {
                'sample_id': sampleId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preview Profile Sample Delete
     * Preview the exact sample population that delete moves to trash.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static previewProfileSampleDeleteProfilesSamplesSampleIdDeletePreviewGet({
        sampleId,
    }: {
        sampleId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles/samples/{sample_id}/delete-preview',
            path: {
                'sample_id': sampleId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Upload Profile Avatar
     * Upload or update avatar image for a profile.
     * @returns VoiceProfileResponse Successful Response
     * @throws ApiError
     */
    public static uploadProfileAvatarProfilesProfileIdAvatarPost({
        profileId,
        formData,
    }: {
        profileId: string,
        formData: Body_upload_profile_avatar_profiles__profile_id__avatar_post,
    }): CancelablePromise<VoiceProfileResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/profiles/{profile_id}/avatar',
            path: {
                'profile_id': profileId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Profile Avatar
     * Get avatar image for a profile.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getProfileAvatarProfilesProfileIdAvatarGet({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles/{profile_id}/avatar',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Profile Avatar
     * Move an avatar to recoverable trash.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static deleteProfileAvatarProfilesProfileIdAvatarDelete({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/profiles/{profile_id}/avatar',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preview Profile Avatar Delete
     * Preview the exact avatar population that delete moves to trash.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static previewProfileAvatarDeleteProfilesProfileIdAvatarDeletePreviewGet({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles/{profile_id}/avatar/delete-preview',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Profile
     * Export a voice profile as a ZIP archive.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static exportProfileProfilesProfileIdExportGet({
        profileId,
        includeSamples = false,
    }: {
        profileId: string,
        includeSamples?: boolean,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles/{profile_id}/export',
            path: {
                'profile_id': profileId,
            },
            query: {
                'include_samples': includeSamples,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Channels
     * List all audio channels.
     * @returns AudioChannelResponse Successful Response
     * @throws ApiError
     */
    public static listChannelsChannelsGet(): CancelablePromise<Array<AudioChannelResponse>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/channels',
        });
    }
    /**
     * Create Channel
     * Create a new audio channel.
     * @returns AudioChannelResponse Successful Response
     * @throws ApiError
     */
    public static createChannelChannelsPost({
        requestBody,
    }: {
        requestBody: AudioChannelCreate,
    }): CancelablePromise<AudioChannelResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/channels',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Channel
     * Get an audio channel by ID.
     * @returns AudioChannelResponse Successful Response
     * @throws ApiError
     */
    public static getChannelChannelsChannelIdGet({
        channelId,
    }: {
        channelId: string,
    }): CancelablePromise<AudioChannelResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/channels/{channel_id}',
            path: {
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Channel
     * Update an audio channel.
     * @returns AudioChannelResponse Successful Response
     * @throws ApiError
     */
    public static updateChannelChannelsChannelIdPut({
        channelId,
        requestBody,
    }: {
        channelId: string,
        requestBody: AudioChannelUpdate,
    }): CancelablePromise<AudioChannelResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/channels/{channel_id}',
            path: {
                'channel_id': channelId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Channel
     * Delete an audio channel.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static deleteChannelChannelsChannelIdDelete({
        channelId,
    }: {
        channelId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/channels/{channel_id}',
            path: {
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Channel Voices
     * Get list of profile IDs assigned to a channel.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getChannelVoicesChannelsChannelIdVoicesGet({
        channelId,
    }: {
        channelId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/channels/{channel_id}/voices',
            path: {
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Set Channel Voices
     * Set which voices are assigned to a channel.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static setChannelVoicesChannelsChannelIdVoicesPut({
        channelId,
        requestBody,
    }: {
        channelId: string,
        requestBody: ChannelVoiceAssignment,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/channels/{channel_id}/voices',
            path: {
                'channel_id': channelId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Profile Channels
     * Get list of channel IDs assigned to a profile.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getProfileChannelsProfilesProfileIdChannelsGet({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/profiles/{profile_id}/channels',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Set Profile Channels
     * Set which channels a profile is assigned to.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static setProfileChannelsProfilesProfileIdChannelsPut({
        profileId,
        requestBody,
    }: {
        profileId: string,
        requestBody: ProfileChannelAssignment,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/profiles/{profile_id}/channels',
            path: {
                'profile_id': profileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Generate Speech
     * Generate speech from text using a voice profile.
     * @returns GenerationResponse Successful Response
     * @throws ApiError
     */
    public static generateSpeechGeneratePost({
        requestBody,
    }: {
        requestBody: GenerationRequest,
    }): CancelablePromise<GenerationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/generate',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List History
     * List generation history with optional filters.
     * @returns HistoryListResponse Successful Response
     * @throws ApiError
     */
    public static listHistoryHistoryGet({
        profileId,
        search,
        limit = 50,
        offset,
    }: {
        profileId?: (string | null),
        search?: (string | null),
        limit?: number,
        offset?: number,
    }): CancelablePromise<HistoryListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/history',
            query: {
                'profile_id': profileId,
                'search': search,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Stats
     * Get generation statistics.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getStatsHistoryStatsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/history/stats',
        });
    }
    /**
     * Get Generation
     * Get a generation by ID.
     * @returns HistoryResponse Successful Response
     * @throws ApiError
     */
    public static getGenerationHistoryGenerationIdGet({
        generationId,
    }: {
        generationId: string,
    }): CancelablePromise<HistoryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/history/{generation_id}',
            path: {
                'generation_id': generationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Generation
     * Move a generation's exact product files to recoverable trash.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static deleteGenerationHistoryGenerationIdDelete({
        generationId,
    }: {
        generationId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/history/{generation_id}',
            path: {
                'generation_id': generationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preview Generation Delete
     * Preview the exact product-owned files that delete will move to trash.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static previewGenerationDeleteHistoryGenerationIdDeletePreviewGet({
        generationId,
    }: {
        generationId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/history/{generation_id}/delete-preview',
            path: {
                'generation_id': generationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Generation
     * Export a generation as a ZIP archive.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static exportGenerationHistoryGenerationIdExportGet({
        generationId,
    }: {
        generationId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/history/{generation_id}/export',
            path: {
                'generation_id': generationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Generation Audio
     * Export only the audio file from a generation.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static exportGenerationAudioHistoryGenerationIdExportAudioGet({
        generationId,
    }: {
        generationId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/history/{generation_id}/export-audio',
            path: {
                'generation_id': generationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Transcribe Audio
     * Transcribe audio file to text.
     * @returns TranscriptionResponse Successful Response
     * @throws ApiError
     */
    public static transcribeAudioTranscribePost({
        formData,
    }: {
        formData: Body_transcribe_audio_transcribe_post,
    }): CancelablePromise<TranscriptionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/transcribe',
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Stories
     * List all stories.
     * @returns StoryResponse Successful Response
     * @throws ApiError
     */
    public static listStoriesStoriesGet(): CancelablePromise<Array<StoryResponse>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/stories',
        });
    }
    /**
     * Create Story
     * Create a new story.
     * @returns StoryResponse Successful Response
     * @throws ApiError
     */
    public static createStoryStoriesPost({
        requestBody,
    }: {
        requestBody: StoryCreate,
    }): CancelablePromise<StoryResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/stories',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Story
     * Get a story with all its items.
     * @returns StoryDetailResponse Successful Response
     * @throws ApiError
     */
    public static getStoryStoriesStoryIdGet({
        storyId,
    }: {
        storyId: string,
    }): CancelablePromise<StoryDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/stories/{story_id}',
            path: {
                'story_id': storyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Story
     * Update a story.
     * @returns StoryResponse Successful Response
     * @throws ApiError
     */
    public static updateStoryStoriesStoryIdPut({
        storyId,
        requestBody,
    }: {
        storyId: string,
        requestBody: StoryCreate,
    }): CancelablePromise<StoryResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/stories/{story_id}',
            path: {
                'story_id': storyId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Story
     * Archive a story and its items in private recoverable trash.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static deleteStoryStoriesStoryIdDelete({
        storyId,
    }: {
        storyId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/stories/{story_id}',
            path: {
                'story_id': storyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preview Story Delete
     * Preview the exact story records archived by deletion.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static previewStoryDeleteStoriesStoryIdDeletePreviewGet({
        storyId,
    }: {
        storyId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/stories/{story_id}/delete-preview',
            path: {
                'story_id': storyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Story Item
     * Add a generation to a story.
     * @returns StoryItemDetail Successful Response
     * @throws ApiError
     */
    public static addStoryItemStoriesStoryIdItemsPost({
        storyId,
        requestBody,
    }: {
        storyId: string,
        requestBody: StoryItemCreate,
    }): CancelablePromise<StoryItemDetail> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/stories/{story_id}/items',
            path: {
                'story_id': storyId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Story Item
     * Remove a story item from a story.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static removeStoryItemStoriesStoryIdItemsItemIdDelete({
        storyId,
        itemId,
    }: {
        storyId: string,
        itemId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/stories/{story_id}/items/{item_id}',
            path: {
                'story_id': storyId,
                'item_id': itemId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Story Item Times
     * Update story item timecodes.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static updateStoryItemTimesStoriesStoryIdItemsTimesPut({
        storyId,
        requestBody,
    }: {
        storyId: string,
        requestBody: StoryItemBatchUpdate,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/stories/{story_id}/items/times',
            path: {
                'story_id': storyId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reorder Story Items
     * Reorder story items and recalculate timecodes.
     * @returns StoryItemDetail Successful Response
     * @throws ApiError
     */
    public static reorderStoryItemsStoriesStoryIdItemsReorderPut({
        storyId,
        requestBody,
    }: {
        storyId: string,
        requestBody: StoryItemReorder,
    }): CancelablePromise<Array<StoryItemDetail>> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/stories/{story_id}/items/reorder',
            path: {
                'story_id': storyId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Move Story Item
     * Move a story item (update position and/or track).
     * @returns StoryItemDetail Successful Response
     * @throws ApiError
     */
    public static moveStoryItemStoriesStoryIdItemsItemIdMovePut({
        storyId,
        itemId,
        requestBody,
    }: {
        storyId: string,
        itemId: string,
        requestBody: StoryItemMove,
    }): CancelablePromise<StoryItemDetail> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/stories/{story_id}/items/{item_id}/move',
            path: {
                'story_id': storyId,
                'item_id': itemId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Trim Story Item
     * Trim a story item (update trim_start_ms and trim_end_ms).
     * @returns StoryItemDetail Successful Response
     * @throws ApiError
     */
    public static trimStoryItemStoriesStoryIdItemsItemIdTrimPut({
        storyId,
        itemId,
        requestBody,
    }: {
        storyId: string,
        itemId: string,
        requestBody: StoryItemTrim,
    }): CancelablePromise<StoryItemDetail> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/stories/{story_id}/items/{item_id}/trim',
            path: {
                'story_id': storyId,
                'item_id': itemId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Split Story Item
     * Split a story item at a given time, creating two clips.
     * @returns StoryItemDetail Successful Response
     * @throws ApiError
     */
    public static splitStoryItemStoriesStoryIdItemsItemIdSplitPost({
        storyId,
        itemId,
        requestBody,
    }: {
        storyId: string,
        itemId: string,
        requestBody: StoryItemSplit,
    }): CancelablePromise<Array<StoryItemDetail>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/stories/{story_id}/items/{item_id}/split',
            path: {
                'story_id': storyId,
                'item_id': itemId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Duplicate Story Item
     * Duplicate a story item, creating a copy with all properties.
     * @returns StoryItemDetail Successful Response
     * @throws ApiError
     */
    public static duplicateStoryItemStoriesStoryIdItemsItemIdDuplicatePost({
        storyId,
        itemId,
    }: {
        storyId: string,
        itemId: string,
    }): CancelablePromise<StoryItemDetail> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/stories/{story_id}/items/{item_id}/duplicate',
            path: {
                'story_id': storyId,
                'item_id': itemId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Story Audio
     * Export story as single mixed audio file with timecode-based mixing.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static exportStoryAudioStoriesStoryIdExportAudioGet({
        storyId,
    }: {
        storyId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/stories/{story_id}/export-audio',
            path: {
                'story_id': storyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Audio
     * Serve generated audio file.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getAudioAudioGenerationIdGet({
        generationId,
    }: {
        generationId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/audio/{generation_id}',
            path: {
                'generation_id': generationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Sample Audio
     * Serve profile sample audio file.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getSampleAudioSamplesSampleIdGet({
        sampleId,
    }: {
        sampleId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/samples/{sample_id}',
            path: {
                'sample_id': sampleId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Unload Model
     * Ask both providers to release residency without touching model payloads.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static unloadModelModelsUnloadPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/models/unload',
        });
    }
    /**
     * Get Model Status
     * Render provider-reported installed models; never inspect mutable caches.
     * @returns ModelStatusListResponse Successful Response
     * @throws ApiError
     */
    public static getModelStatusModelsStatusGet(): CancelablePromise<ModelStatusListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/models/status',
        });
    }
    /**
     * Clear Cache
     * Clear all voice prompt caches (memory and disk).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static clearCacheCacheClearPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/cache/clear',
        });
    }
}

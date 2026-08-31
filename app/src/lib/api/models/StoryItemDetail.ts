/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Detail model for story item with generation info.
 */
export type StoryItemDetail = {
    id: string;
    story_id: string;
    generation_id: string;
    start_time_ms: number;
    track?: number;
    trim_start_ms?: number;
    trim_end_ms?: number;
    created_at: string;
    profile_id: string;
    profile_name: string;
    text: string;
    language: string;
    audio_path: string;
    duration: number;
    seed: (number | null);
    instruct: (string | null);
    generation_created_at: string;
};

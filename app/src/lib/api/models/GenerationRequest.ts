/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for voice generation.
 */
export type GenerationRequest = {
    profile_id: string;
    text: string;
    language?: string;
    seed?: (number | null);
    instruct?: (string | null);
    consent_asserted?: boolean;
    mode?: GenerationRequest.mode;
    voice_id?: (string | null);
    description?: (string | null);
};
export namespace GenerationRequest {
    export enum mode {
        PROMPT_CLONE = 'prompt_clone',
        NAMED_VOICE = 'named_voice',
        VOICE_DESIGN = 'voice_design',
    }
}

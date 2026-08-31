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
};

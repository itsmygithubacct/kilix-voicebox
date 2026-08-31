/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export const $HealthResponse = {
    description: `Response model for health check.`,
    properties: {
        status: {
            type: 'string',
            isRequired: true,
        },
        qwen_provider: {
            type: 'string',
        },
        transcribe_provider: {
            type: 'string',
        },
    },
} as const;

/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export const $GenerationRequest = {
    description: `Request model for voice generation.`,
    properties: {
        profile_id: {
            type: 'string',
            isRequired: true,
        },
        text: {
            type: 'string',
            isRequired: true,
            maxLength: 5000,
            minLength: 1,
        },
        language: {
            type: 'string',
            pattern: '^(zh|en|ja|ko|de|fr|ru|pt|es|it)$',
        },
        seed: {
            type: 'any-of',
            contains: [{
                type: 'number',
                exclusiveMaximum: 9007199254740992,
            }, {
                type: 'null',
            }],
        },
        instruct: {
            type: 'any-of',
            contains: [{
                type: 'string',
                maxLength: 500,
            }, {
                type: 'null',
            }],
        },
        consent_asserted: {
            type: 'boolean',
        },
        mode: {
            type: 'Enum',
        },
        voice_id: {
            type: 'any-of',
            contains: [{
                type: 'string',
                maxLength: 64,
                minLength: 1,
            }, {
                type: 'null',
            }],
        },
        description: {
            type: 'any-of',
            contains: [{
                type: 'string',
                maxLength: 4096,
                minLength: 1,
            }, {
                type: 'null',
            }],
        },
    },
} as const;

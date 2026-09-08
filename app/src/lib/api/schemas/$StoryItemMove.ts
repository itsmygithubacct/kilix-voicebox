/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export const $StoryItemMove = {
    description: `Request model for moving a story item (position and/or track).`,
    properties: {
        start_time_ms: {
            type: 'number',
            isRequired: true,
            maximum: 900000,
        },
        track: {
            type: 'number',
            maximum: 255,
        },
    },
} as const;

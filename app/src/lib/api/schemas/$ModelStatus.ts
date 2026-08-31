/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export const $ModelStatus = {
    description: `Read-only provider inventory projection.`,
    properties: {
        provider: {
            type: 'string',
            isRequired: true,
        },
        model_id: {
            type: 'string',
            isRequired: true,
        },
        display_name: {
            type: 'string',
            isRequired: true,
        },
        installed: {
            type: 'boolean',
            isRequired: true,
        },
        resident: {
            type: 'boolean',
        },
        installed_bytes: {
            type: 'any-of',
            contains: [{
                type: 'number',
            }, {
                type: 'null',
            }],
        },
    },
} as const;

/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export const $StoryItemReorder = {
    description: `Request model for reordering story items.`,
    properties: {
        item_ids: {
            type: 'array',
            contains: {
                type: 'string',
            },
            isRequired: true,
        },
    },
} as const;

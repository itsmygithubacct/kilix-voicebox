/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Read-only provider inventory projection.
 */
export type ModelStatus = {
    provider: string;
    model_id: string;
    display_name: string;
    installed: boolean;
    resident?: boolean;
    installed_bytes?: (number | null);
};

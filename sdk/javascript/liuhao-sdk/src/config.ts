/**
 * LiuHao AI OS SDK Configuration
 *
 * Handles SDK configuration including default values,
 * environment variable support, and config merging.
 */

import type { LiuHaoConfig } from './types';

/**
 * Default configuration values.
 */
export const DEFAULT_CONFIG: Readonly<LiuHaoConfig> = {
  baseUrl: 'http://localhost:8000',
  apiKey: undefined,
  timeout: 30000,
  retries: 3,
  userAgent: '@liuhao/sdk/1.0.0',
  validateSSL: true,
  defaultHeaders: undefined,
};

/**
 * Environment variable names used by the SDK.
 */
export const ENV_VARS = {
  API_KEY: 'LIUHAO_API_KEY',
  BASE_URL: 'LIUHAO_BASE_URL',
  TIMEOUT: 'LIUHAO_TIMEOUT',
  RETRIES: 'LIUHAO_RETRIES',
} as const;

/**
 * Partial configuration type for user-provided options.
 */
export type LiuHaoConfigInput = Partial<LiuHaoConfig>;

/**
 * Retrieves configuration values from environment variables.
 *
 * Works in both Node.js and browser environments. In browser
 * environments, environment variables are not available.
 *
 * @returns Configuration values extracted from environment variables
 */
export function getEnvConfig(): Partial<LiuHaoConfig> {
  const envConfig: Partial<LiuHaoConfig> = {};

  // Check if we're in a Node.js environment
  if (typeof process !== 'undefined' && process.env) {
    const apiKey = process.env[ENV_VARS.API_KEY];
    if (apiKey !== undefined) {
      envConfig.apiKey = apiKey;
    }

    const baseUrl = process.env[ENV_VARS.BASE_URL];
    if (baseUrl !== undefined) {
      envConfig.baseUrl = baseUrl;
    }

    const timeout = process.env[ENV_VARS.TIMEOUT];
    if (timeout !== undefined) {
      const parsed = Number.parseInt(timeout, 10);
      if (!Number.isNaN(parsed) && parsed > 0) {
        envConfig.timeout = parsed;
      }
    }

    const retries = process.env[ENV_VARS.RETRIES];
    if (retries !== undefined) {
      const parsed = Number.parseInt(retries, 10);
      if (!Number.isNaN(parsed) && parsed >= 0) {
        envConfig.retries = parsed;
      }
    }
  }

  return envConfig;
}

/**
 * Merges configuration from multiple sources with proper priority.
 *
 * Priority (highest to lowest):
 * 1. User-provided config (explicit parameters)
 * 2. Environment variables
 * 3. Default values
 *
 * @param userConfig - User-provided configuration
 * @returns Fully resolved configuration
 *
 * @example
 * ```ts
 * const config = resolveConfig({ apiKey: 'my-key' });
 * // config.baseUrl = 'http://localhost:8000' (from defaults)
 * // config.apiKey = 'my-key' (from user)
 * // config.timeout = 30000 (from defaults or env)
 * ```
 */
export function resolveConfig(userConfig: LiuHaoConfigInput = {}): LiuHaoConfig {
  const envConfig = getEnvConfig();

  const merged: LiuHaoConfig = {
    baseUrl: userConfig.baseUrl ?? envConfig.baseUrl ?? DEFAULT_CONFIG.baseUrl,
    apiKey: userConfig.apiKey ?? envConfig.apiKey ?? DEFAULT_CONFIG.apiKey,
    timeout: userConfig.timeout ?? envConfig.timeout ?? DEFAULT_CONFIG.timeout,
    retries: userConfig.retries ?? envConfig.retries ?? DEFAULT_CONFIG.retries,
    userAgent: userConfig.userAgent ?? DEFAULT_CONFIG.userAgent,
    validateSSL: userConfig.validateSSL ?? DEFAULT_CONFIG.validateSSL,
    defaultHeaders: userConfig.defaultHeaders ?? DEFAULT_CONFIG.defaultHeaders,
  };

  // Normalize base URL - remove trailing slash
  if (merged.baseUrl.endsWith('/')) {
    merged.baseUrl = merged.baseUrl.slice(0, -1);
  }

  return merged;
}

/**
 * Validates the configuration and throws if required fields are missing.
 *
 * @param config - Configuration to validate
 * @throws {Error} If required configuration is missing or invalid
 */
export function validateConfig(config: LiuHaoConfig): void {
  if (!config.baseUrl || typeof config.baseUrl !== 'string') {
    throw new Error('baseUrl is required and must be a string');
  }

  try {
    // eslint-disable-next-line no-new
    new URL(config.baseUrl);
  } catch {
    throw new Error(`baseUrl "${config.baseUrl}" is not a valid URL`);
  }

  if (config.timeout <= 0) {
    throw new Error('timeout must be a positive number');
  }

  if (config.retries < 0) {
    throw new Error('retries must be a non-negative number');
  }
}

/**
 * Creates a new configuration object by overriding values
 * from an existing config.
 *
 * @param original - Original configuration
 * @param overrides - Values to override
 * @returns New configuration with overrides applied
 */
export function mergeConfig(
  original: LiuHaoConfig,
  overrides: LiuHaoConfigInput,
): LiuHaoConfig {
  const merged = {
    ...original,
    ...overrides,
    defaultHeaders: {
      ...original.defaultHeaders,
      ...overrides.defaultHeaders,
    },
  };

  // Normalize base URL
  if (merged.baseUrl.endsWith('/')) {
    merged.baseUrl = merged.baseUrl.slice(0, -1);
  }

  return merged;
}

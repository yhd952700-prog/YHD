/**
 * Health Resource
 *
 * Provides methods for checking system health and status
 * of the LiuHao AI OS system.
 */

import type { LiuHaoClient } from '../client';
import type {
  DetailedHealthResult,
  HealthCheckResult,
  HealthStatus,
} from '../types';

/**
 * Resource class for health check API operations.
 *
 * Use this resource to monitor the health of the LiuHao AI OS
 * system, including overall health, readiness, liveness, and
 * detailed component status.
 *
 * @example
 * ```ts
 * const client = new LiuHaoClient({ baseUrl: '...' });
 *
 * // Simple health check
 * const health = await client.health.health();
 * if (health.status === HealthStatus.HEALTHY) {
 *   console.log('System is healthy');
 * }
 * ```
 */
export class HealthResource {
  private readonly client: LiuHaoClient;

  /**
   * @param client - LiuHaoClient instance
   */
  constructor(client: LiuHaoClient) {
    this.client = client;
  }

  /**
   * Perform a basic health check.
   *
   * Returns the overall health status of the system.
   * This is a lightweight check suitable for load balancers.
   *
   * @returns Basic health check result
   *
   * @example
   * ```ts
   * const result = await client.health.health();
   * console.log(result.status); // 'healthy', 'degraded', or 'unhealthy'
   * console.log(result.timestamp);
   * ```
   */
  async health(): Promise<HealthCheckResult> {
    return this.client.get<HealthCheckResult>('/api/v1/health');
  }

  /**
   * Check if the system is ready to serve requests.
   *
   * Returns 200 when all required components are initialized
   * and the system can accept traffic. Returns 503 otherwise.
   *
   * @returns Readiness check result
   *
   * @example
   * ```ts
   * const result = await client.health.ready();
   * if (result.status === HealthStatus.HEALTHY) {
   *   // System is ready to accept requests
   * }
   * ```
   */
  async ready(): Promise<HealthCheckResult> {
    return this.client.get<HealthCheckResult>('/api/v1/health/ready');
  }

  /**
   * Check if the system is alive.
   *
   * A simple liveness probe that returns 200 as long as the
   * service process is running. Useful for Kubernetes liveness
   * probes.
   *
   * @returns Liveness check result
   *
   * @example
   * ```ts
   * try {
   *   await client.health.liveness();
   *   // Service is alive
   * } catch (error) {
   *   // Service may need to be restarted
   * }
   * ```
   */
  async liveness(): Promise<HealthCheckResult> {
    return this.client.get<HealthCheckResult>('/api/v1/health/live');
  }

  /**
   * Get detailed health information with component breakdown.
   *
   * Returns the health status of all system components along
   * with system resource usage information.
   *
   * @returns Detailed health result with components
   *
   * @example
   * ```ts
   * const result = await client.health.detailed();
   *
   * console.log(`Status: ${result.status}`);
   * console.log(`Uptime: ${result.uptimeSeconds}s`);
   *
   * // Check individual components
   * Object.entries(result.components).forEach(([name, component]) => {
   *   console.log(`${name}: ${component.status}`);
   * });
   *
   * // System stats
   * if (result.system) {
   *   console.log(`CPU: ${result.system.cpuUsagePercent}%`);
   *   console.log(`Memory: ${result.system.memoryUsageBytes} bytes`);
   * }
   * ```
   */
  async detailed(): Promise<DetailedHealthResult> {
    return this.client.get<DetailedHealthResult>('/api/v1/health/detailed');
  }

  /**
   * Get health status of specific components.
   *
   * Returns the health status of one or more specific system
   * components.
   *
   * @param componentNames - Optional list of component names to check.
   *   If not provided, returns all components.
   * @returns Map of component names to their health status
   *
   * @example
   * ```ts
   * // Check all components
   * const allComponents = await client.health.components();
   *
   * // Check specific components
   * const specific = await client.health.components(['database', 'cache']);
   * console.log(specific.database.status);
   * ```
   */
  async components(componentNames?: string[]): Promise<Record<string, HealthStatus>> {
    const params: Record<string, unknown> = {};

    if (componentNames !== undefined && componentNames.length > 0) {
      params.components = componentNames;
    }

    const response = await this.client.get<{
      components: Record<string, { status: HealthStatus }>;
    }>('/api/v1/health/components', params);

    // Convert to a simpler format: name -> status
    const result: Record<string, HealthStatus> = {};
    for (const [name, component] of Object.entries(response.components)) {
      result[name] = component.status;
    }
    return result;
  }
}

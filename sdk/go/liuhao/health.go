package liuhao

import (
	"context"
	"net/http"
)

// HealthService provides methods for checking system health of the LiuHao AI OS.
//
// Use Client.Health to access this service. The health service allows
// monitoring the overall system status, individual components, and
// readiness for receiving traffic.
type HealthService struct {
	client *Client
}

// Check performs a basic health check of the LiuHao AI OS.
//
// Returns the overall health status, version, and uptime information.
// This is a lightweight check suitable for monitoring and load balancer
// health probes.
//
// Example:
//
//	health, err := client.Health.Check(ctx)
//	if err != nil {
//	    log.Fatalf("Health check failed: %v", err)
//	}
//	fmt.Printf("System status: %s (version: %s)\n", health.Status, health.Version)
func (s *HealthService) Check(ctx context.Context) (*HealthCheck, error) {
	path := "/health"

	var result HealthCheck
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Ready checks whether the system is ready to serve requests.
//
// This endpoint returns 200 OK when all critical components are ready,
// and 503 Service Unavailable otherwise. Use this for readiness probes
// in Kubernetes or similar orchestration systems.
//
// Example:
//
//	ready, err := client.Health.Ready(ctx)
//	if err != nil {
//	    fmt.Println("System is not ready")
//	    return
//	}
//	if ready {
//	    fmt.Println("System is ready to serve traffic")
//	}
func (s *HealthService) Ready(ctx context.Context) (bool, error) {
	path := "/health/ready"

	var result struct {
		Ready bool `json:"ready"`
	}
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return false, err
	}
	return result.Ready, nil
}

// Liveness checks whether the system is alive and responding.
//
// This is a very lightweight check that simply verifies the service is
// running. Use this for liveness probes in container orchestration.
//
// Example:
//
//	alive, err := client.Health.Liveness(ctx)
//	if err != nil {
//	    fmt.Println("Service is not responding")
//	    return
//	}
//	if alive {
//	    fmt.Println("Service is alive")
//	}
func (s *HealthService) Liveness(ctx context.Context) (bool, error) {
	path := "/health/live"

	var result struct {
		Alive bool `json:"alive"`
	}
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return false, err
	}
	return result.Alive, nil
}

// Detailed performs a comprehensive health check of all system components.
//
// This returns detailed health information for each component including
// database, Redis, storage, LLM provider, vector database, workers,
// and system resources.
//
// Example:
//
//	health, err := client.Health.Detailed(ctx)
//	if err != nil {
//	    log.Fatal(err)
//	}
//	fmt.Printf("Overall status: %s\n", health.Status)
//	if health.Database != nil {
//	    fmt.Printf("Database: %s (latency: %dms)\n",
//	        health.Database.Status, health.Database.LatencyMs)
//	}
func (s *HealthService) Detailed(ctx context.Context) (*DetailedHealth, error) {
	path := "/health/detailed"

	var result DetailedHealth
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Components retrieves the health status of individual system components.
//
// This returns a map of component names to their health status, including
// latency information and last check time.
//
// Example:
//
//	components, err := client.Health.Components(ctx)
//	if err != nil {
//	    log.Fatal(err)
//	}
//	for name, component := range components {
//	    fmt.Printf("%s: %s\n", name, component.Status)
//	}
func (s *HealthService) Components(ctx context.Context) (map[string]ComponentHealth, error) {
	path := "/health/components"

	var result struct {
		Components map[string]ComponentHealth `json:"components"`
	}
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return result.Components, nil
}

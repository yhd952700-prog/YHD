# LiuHao AI OS - Production Runbook

## Overview
This runbook provides operational procedures for the LiuHao AI OS in production.

## Service Architecture

```
┌─────────────────┐
│   Load Balancer │
└────────┬────────┘
         │
┌────────▼────────┐
│   API Gateway   │ ← FastAPI, port 8080
└────────┬────────┘
         │
    ┌────┴────┬─────────┬──────────┐
    ▼         ▼         ▼          ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌────────┐
│ Auth  │ │ Plugins │ │ Models │ │ Memory │
└───────┘ └───────┘ └───────┘ └────────┘
```

## Critical Services

| Service | Port | Health Check | Dependencies |
|---------|------|--------------|--------------|
| API Gateway | 8080 | /health, /ready | Redis, Database |
| Redis | 6379 | PING | - |
| Database | 5432 | SELECT 1 | - |
| Prometheus | 9090 | /-/healthy | - |
| Grafana | 3000 | /api/health | Prometheus |

## Common Operations

### Start Services
```bash
# Using Docker Compose
docker-compose -f docker-compose.prod.yml up -d

# Using Kubernetes
kubectl apply -f k8s/
```

### Stop Services
```bash
# Graceful shutdown
docker-compose -f docker-compose.prod.yml down

# Kubernetes
kubectl delete -f k8s/
```

### Check Service Health
```bash
# API Gateway
curl http://localhost:8080/health
curl http://localhost:8080/ready

# Redis
redis-cli ping

# Database
psql $DATABASE_URL -c "SELECT 1"
```

## Incident Response

### Service Down
1. Check service status: `systemctl status liuhao-ai-os` or `docker ps`
2. Check logs: `docker logs liuhao-api` or `kubectl logs -l app=liuhao-ai-os`
3. Check dependencies: Redis, Database connectivity
4. Restart if needed: `docker restart liuhao-api`

### High Error Rate
1. Check error logs: `grep "ERROR" logs/app.log | tail -50`
2. Check metrics: Grafana dashboard "Error Rate"
3. Check recent deployments: `kubectl rollout history deployment/liuhao-ai-os`
4. Rollback if needed: `kubectl rollout undo deployment/liuhao-ai-os`

### High Latency
1. Check Grafana dashboard "Latency (P50, P95, P99)"
3. Check database slow queries: `pg_stat_statements`
4. Check cache hit rate: Grafana "Cache Hit Rate"
5. Check for resource contention: CPU, Memory, Disk I/O

### High Memory/CPU
1. Check Grafana "Memory Usage" / "CPU Usage"
2. Check for memory leaks: `pprof` or `py-spy`
3. Check for runaway processes: `ps aux --sort=-%mem | head`
4. Restart service if needed

### Database Issues
1. Check connection pool: `db_pool_connections_in_use` vs `db_pool_connections_max`
2. Check slow queries: `SELECT * FROM pg_stat_statements ORDER BY mean_time DESC LIMIT 10`
3. Check locks: `SELECT * FROM pg_locks WHERE NOT granted`
4. Check replication lag (if applicable)

### Redis Issues
1. Check memory: `redis-cli INFO memory`
2. Check connected clients: `redis-cli CLIENT LIST`
3. Check slowlog: `redis-cli SLOWLOG GET 10`
4. Check persistence: `redis-cli LASTSAVE`

## Backup & Recovery

### Create Backup
```bash
python scripts/ops/backup.py create --name "manual_backup_$(date +%Y%m%d)"
```

### List Backups
```bash
python scripts/ops/backup.py list
```

### Restore Backup
```bash
python scripts/ops/backup.py restore --backup backups/backup_20240115_020000.tar.gz
```

### Automated Backup Schedule
- Daily at 2:00 AM (configurable via `backup.schedule`)
- Retention: 30 days (configurable via `backup.retention_days`)

## Rollback Procedures

### Configuration Rollback
```bash
# List checkpoints
python scripts/ops/rollback.py list

# Create checkpoint before changes
python scripts/ops/rollback.py checkpoint --name "pre_config_change" --description "Before config update"

# Rollback to checkpoint
python scripts/ops/rollback.py rollback --checkpoint pre_config_change_20240115_143000
```

### Deployment Rollback
```bash
# Kubernetes
kubectl rollout undo deployment/liuhao-ai-os

# Specific revision
kubectl rollout undo deployment/liuhao-ai-os --to-revision=5

# Check rollout status
kubectl rollout status deployment/liuhao-ai-os
```

### Database Rollback
```bash
# Point-in-time recovery (if using PostgreSQL)
pg_basebackup -D /var/lib/postgresql/data -Ft -z -P

# Or restore from backup
pg_restore -d liuhao_ai_os backup.dump
```

## Security Operations

### Rotate API Keys
```bash
# List keys
python -m src.security.api_keys list_keys

# Create new key
python -m src.security.api_keys create_key --name "new_key" --scopes provider,agent --expires-in-days 90

# Rotate existing key
python -m src.security.api_keys rotate_key --key-id <key_id>
```

### JWT Secret Rotation
1. Generate new secret: `openssl rand -base64 32`
2. Update `JWT_SECRET_KEY` environment variable
2. Restart all services
3. Invalidate existing tokens (optional)

### Certificate Renewal
```bash
# Check certificate expiry
openssl x509 -in certs/server.crt -text -noout | grep "Not After"

# Renew with Let's Encrypt
certbot renew --cert-name liuhao-ai-os

# Restart services
docker-compose -f docker-compose.prod.yml restart
```

## Scaling Operations

### Horizontal Scaling
```bash
# Kubernetes
kubectl scale deployment liuhao-ai-os --replicas=5

# Docker Compose
docker-compose -f docker-compose.prod.yml up -d --scale api=3
```

### Vertical Scaling
```bash
# Kubernetes - update resource limits
kubectl patch deployment liuhao-ai-os -p '{"spec":{"template":{"spec":{"containers":[{"name":"api","resources":{"limits":{"memory":"4Gi","cpu":"2000m"}}}]}}}}'
```

## Log Management

### Log Locations
- Application: `logs/app.log` (JSON format)
- Access: `logs/access.log`
- Audit: `data/security/audit.log`
- Error: Filter `logs/app.log` for ERROR level

### Log Rotation
- Max size: 100 MB per file
- Retention: 10 files
- Compression: Enabled

### Query Logs
```bash
# Search errors
grep '"level": "ERROR"' logs/app.log | jq .

# Filter by trace ID
grep "trace_id=\"abc123\"" logs/app.log

# Recent errors
tail -100 logs/app.log | grep ERROR
```

## Monitoring & Alerting

### Key Dashboards
- Overview: System health, request rate, latency, error rate
- Resources: CPU, Memory, Disk, Network
- Application: Request rate, Latency (P50/P95/P99), Error rate
- Security: Auth failures, Security violations
- Plugins: Plugin health, Resource usage
- Database: Connections, Slow queries, Latency

### Key Alerts
| Alert | Severity | Action |
|-------|----------|--------|
| ServiceDown | Critical | Immediate investigation |
| HighErrorRate | Critical | Check logs, consider rollback |
| HighLatency | Warning | Check resources, DB, cache |
| HighLatencyP99 | Critical | Urgent investigation |
| SecurityViolationDetected | Critical | Immediate investigation |
| HighFailedAuthRate | Warning | Check for brute force |

### Silencing Alerts
```bash
# Via Alertmanager API
curl -X POST http://alertmanager:9093/api/v1/silences \
  -d '{"matchers":[{"name":"alertname","value":"HighLatency"}],"startsAt":"2024-01-15T10:00:00Z","endsAt":"2024-01-15T12:00:00Z"}'
```

## Maintenance Windows

### Scheduled Maintenance
- Weekly: Sunday 02:00-04:00 UTC
- Tasks: Security patches, dependency updates, backup verification

### Emergency Maintenance
- Authorization: On-call engineer + team lead approval
- Communication: #ops channel, status page update
- Rollback plan: Required before starting

## Contact Information

| Role | Contact | Escalation |
|------|---------|------------|
| On-call Engineer | +1-XXX-XXX-XXXX | Primary |
| Team Lead | +1-XXX-XXX-XXXX | Secondary |
| Security Team | security@liuhao.example.com | Security incidents |
| Infrastructure | infra@liuhao.example.com | Infrastructure issues |

## Useful Commands Quick Reference

```bash
# View all pods
kubectl get pods -n liuhao-ai-os

# View logs
kubectl logs -l app=liuhao-ai-os -n liuhao-ai-os --tail=100 -f

# Describe pod
kubectl describe pod -l app=liuhao-ai-os -n liuhao-ai-os

# Exec into pod
kubectl exec -it -l app=liuhao-ai-os -n liuhao-ai-os -- /bin/bash

# Port forward
kubectl port-forward -n liuhao-ai-os svc/liuhao-ai-os 8080:8080

# View events
kubectl get events -n liuhao-ai-os --sort-by='.lastTimestamp'

# Check resource usage
kubectl top pods -n liuhao-ai-os
kubectl top nodes
```

## Version Information

- Document Version: 1.0.0
- Last Updated: 2024-01-15
- Next Review: 2024-04-15
- Owner: Platform Team
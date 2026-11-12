use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Status of an AI Agent.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AgentStatus {
    Idle,
    Running,
    Paused,
    Stopped,
    Error,
    Training,
    Deploying,
}

/// Represents an AI Agent in the LiuHao platform.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Agent {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
    pub status: AgentStatus,
    #[serde(rename = "model_name")]
    pub model_name: String,
    #[serde(rename = "system_prompt")]
    #[serde(default)]
    pub system_prompt: Option<String>,
    #[serde(default)]
    pub tools: Vec<String>,
    #[serde(rename = "knowledge_bases")]
    #[serde(default)]
    pub knowledge_bases: Vec<String>,
    #[serde(default)]
    pub metadata: Option<HashMap<String, serde_json::Value>>,
    #[serde(rename = "created_at")]
    pub created_at: DateTime<Utc>,
    #[serde(rename = "updated_at")]
    pub updated_at: DateTime<Utc>,
}

/// Request to create a new agent.
#[derive(Debug, Clone, Serialize, Default)]
pub struct CreateAgentRequest {
    pub name: String,
    #[serde(rename = "model_name")]
    pub model_name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(rename = "system_prompt")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub system_prompt: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub tools: Vec<String>,
    #[serde(rename = "knowledge_bases")]
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub knowledge_bases: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<HashMap<String, serde_json::Value>>,
}

/// Represents a memory item in the LiuHao memory system.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryItem {
    pub id: String,
    pub content: String,
    #[serde(rename = "memory_type")]
    pub memory_type: String,
    #[serde(default)]
    pub category: Option<String>,
    pub importance: f64,
    #[serde(default)]
    pub metadata: Option<HashMap<String, serde_json::Value>>,
    #[serde(rename = "user_id")]
    #[serde(default)]
    pub user_id: Option<String>,
    #[serde(rename = "agent_id")]
    #[serde(default)]
    pub agent_id: Option<String>,
    #[serde(rename = "created_at")]
    pub created_at: DateTime<Utc>,
    #[serde(rename = "updated_at")]
    pub updated_at: DateTime<Utc>,
    #[serde(rename = "last_accessed_at")]
    #[serde(default)]
    pub last_accessed_at: Option<DateTime<Utc>>,
    #[serde(rename = "access_count")]
    pub access_count: i32,
}

/// Request to store a new memory item.
#[derive(Debug, Clone, Serialize, Default)]
pub struct StoreMemoryRequest {
    pub content: String,
    #[serde(rename = "memory_type")]
    pub memory_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub category: Option<String>,
    #[serde(default)]
    pub importance: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<HashMap<String, serde_json::Value>>,
    #[serde(rename = "user_id")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_id: Option<String>,
    #[serde(rename = "agent_id")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
}

/// Request to search memory.
#[derive(Debug, Clone, Serialize, Default)]
pub struct SearchMemoryRequest {
    pub query: String,
    #[serde(rename = "memory_type")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub category: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit: Option<u32>,
}

/// Represents a knowledge base.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeBase {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(rename = "vector_dimension")]
    pub vector_dimension: i32,
    #[serde(rename = "embedding_model")]
    pub embedding_model: String,
    #[serde(rename = "document_count")]
    pub document_count: i32,
    #[serde(rename = "chunk_count")]
    pub chunk_count: i32,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub metadata: Option<HashMap<String, serde_json::Value>>,
    #[serde(rename = "created_at")]
    pub created_at: DateTime<Utc>,
    #[serde(rename = "updated_at")]
    pub updated_at: DateTime<Utc>,
}

/// Request to create a knowledge base.
#[derive(Debug, Clone, Serialize, Default)]
pub struct CreateKnowledgeBaseRequest {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(rename = "embedding_model")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub embedding_model: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
}

/// Request to search a knowledge base.
#[derive(Debug, Clone, Serialize, Default)]
pub struct SearchKnowledgeRequest {
    pub query: String,
    #[serde(rename = "top_k")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub top_k: Option<u32>,
    #[serde(rename = "min_score")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub min_score: Option<f64>,
}

/// A search result from a knowledge base.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeSearchResult {
    pub id: String,
    pub document_id: String,
    pub content: String,
    pub score: f64,
    #[serde(default)]
    pub metadata: Option<HashMap<String, serde_json::Value>>,
}

/// Task status.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskStatus {
    Pending,
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
    Timeout,
}

/// Represents an asynchronous task.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: String,
    #[serde(rename = "task_type")]
    pub task_type: String,
    pub status: TaskStatus,
    pub progress: u32,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(rename = "agent_id")]
    #[serde(default)]
    pub agent_id: Option<String>,
    #[serde(rename = "result_data")]
    #[serde(default)]
    pub result_data: Option<HashMap<String, serde_json::Value>>,
    #[serde(default)]
    pub metadata: Option<HashMap<String, serde_json::Value>>,
    #[serde(rename = "created_at")]
    pub created_at: DateTime<Utc>,
    #[serde(rename = "started_at")]
    #[serde(default)]
    pub started_at: Option<DateTime<Utc>>,
    #[serde(rename = "completed_at")]
    #[serde(default)]
    pub completed_at: Option<DateTime<Utc>>,
}

impl Task {
    /// Returns true if the task is in a terminal state.
    pub fn is_done(&self) -> bool {
        matches!(
            self.status,
            TaskStatus::Completed
                | TaskStatus::Failed
                | TaskStatus::Cancelled
                | TaskStatus::Timeout
        )
    }

    /// Returns true if the task completed successfully.
    pub fn is_success(&self) -> bool {
        matches!(self.status, TaskStatus::Completed)
    }
}

/// Overall health status.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OverallStatus {
    Healthy,
    Degraded,
    Unhealthy,
    Maintenance,
}

/// Health status of the LiuHao platform.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthStatus {
    pub status: OverallStatus,
    pub version: String,
    #[serde(rename = "build_time")]
    #[serde(default)]
    pub build_time: Option<String>,
    #[serde(default)]
    pub uptime: Option<String>,
    #[serde(rename = "service_count")]
    pub service_count: u32,
    #[serde(rename = "active_connections")]
    pub active_connections: u32,
    #[serde(default)]
    pub services: Option<HashMap<String, ServiceHealth>>,
    #[serde(rename = "checked_at")]
    pub checked_at: DateTime<Utc>,
}

impl HealthStatus {
    /// Returns true if the overall status is healthy.
    pub fn is_healthy(&self) -> bool {
        matches!(self.status, OverallStatus::Healthy)
    }
}

/// Health status of an individual service.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceHealth {
    pub status: OverallStatus,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(rename = "latency_ms")]
    pub latency_ms: u32,
    #[serde(rename = "last_check")]
    pub last_check: DateTime<Utc>,
}

impl ServiceHealth {
    /// Returns true if the service is healthy.
    pub fn is_healthy(&self) -> bool {
        matches!(self.status, OverallStatus::Healthy)
    }
}

/// Paginated response wrapper.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaginatedResponse<T> {
    pub data: Vec<T>,
    #[serde(default)]
    pub cursor: Option<String>,
    #[serde(rename = "has_more")]
    pub has_more: bool,
    #[serde(default)]
    pub total: Option<u64>,
}

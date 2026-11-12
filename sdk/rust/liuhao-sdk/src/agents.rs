use crate::client::LiuHaoConfig;
use crate::error::LiuHaoError;
use crate::types::{Agent, AgentStatus, CreateAgentRequest};
use reqwest::Client;
use serde_json::{json, Value};
use std::sync::Arc;

/// Client for the Agents API.
#[derive(Debug, Clone)]
pub struct AgentsClient {
    config: Arc<LiuHaoConfig>,
    http: Client,
}

impl AgentsClient {
    pub(crate) fn new(config: Arc<LiuHaoConfig>) -> Self {
        let http = Client::builder()
            .timeout(config.timeout)
            .user_agent(&config.user_agent)
            .build()
            .expect("Failed to build HTTP client");
        Self { config, http }
    }

    /// Lists all agents.
    pub async fn list(&self) -> Result<Vec<Agent>, LiuHaoError> {
        let url = self.build_url("agents")?;
        self.do_get::<Vec<Agent>>(&url).await
    }

    /// Lists agents with pagination.
    pub async fn list_with_options(
        &self,
        limit: Option<u32>,
        cursor: Option<&str>,
    ) -> Result<Vec<Agent>, LiuHaoError> {
        let mut url = self.build_url("agents")?;
        {
            let mut query = url.query_pairs_mut();
            if let Some(l) = limit {
                query.append_pair("limit", &l.to_string());
            }
            if let Some(c) = cursor {
                query.append_pair("cursor", c);
            }
        }
        self.do_get::<Vec<Agent>>(&url).await
    }

    /// Gets an agent by ID.
    pub async fn get(&self, agent_id: &str) -> Result<Agent, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}", agent_id))?;
        self.do_get(&url).await
    }

    /// Creates a new agent.
    pub async fn create(&self, request: CreateAgentRequest) -> Result<Agent, LiuHaoError> {
        let url = self.build_url("agents")?;
        self.do_post(&url, &request).await
    }

    /// Creates an agent with just a name and model.
    pub async fn create_simple(&self, name: &str, model_name: &str) -> Result<Agent, LiuHaoError> {
        let request = CreateAgentRequest {
            name: name.to_string(),
            model_name: model_name.to_string(),
            ..Default::default()
        };
        self.create(request).await
    }

    /// Updates an agent.
    pub async fn update(&self, agent_id: &str, agent: &Agent) -> Result<Agent, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}", agent_id))?;
        self.do_put(&url, agent).await
    }

    /// Partially updates an agent.
    pub async fn patch(
        &self,
        agent_id: &str,
        updates: Value,
    ) -> Result<Agent, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}", agent_id))?;
        self.do_patch(&url, &updates).await
    }

    /// Deletes an agent.
    pub async fn delete(&self, agent_id: &str) -> Result<(), LiuHaoError> {
        let url = self.build_url(&format!("agents/{}", agent_id))?;
        self.do_delete::<Value>(&url).await?;
        Ok(())
    }

    /// Executes an agent with the given input.
    pub async fn execute(&self, agent_id: &str, input: &str) -> Result<String, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}/execute", agent_id))?;
        let body = json!({ "input": input });
        let result: Value = self.do_post(&url, &body).await?;
        Ok(result
            .get("output")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string())
    }

    /// Executes an agent with full parameters.
    pub async fn execute_with_params(
        &self,
        agent_id: &str,
        params: Value,
    ) -> Result<Value, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}/execute", agent_id))?;
        self.do_post(&url, &params).await
    }

    /// Gets the status of an agent.
    pub async fn status(&self, agent_id: &str) -> Result<AgentStatus, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}/status", agent_id))?;
        let agent: Agent = self.do_get(&url).await?;
        Ok(agent.status)
    }

    /// Cancels a running agent.
    pub async fn cancel(&self, agent_id: &str) -> Result<(), LiuHaoError> {
        let url = self.build_url(&format!("agents/{}/cancel", agent_id))?;
        self.do_post::<Value, _>(&url, &json!({})).await?;
        Ok(())
    }

    /// Gets metrics for an agent.
    pub async fn metrics(&self, agent_id: &str) -> Result<Value, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}/metrics", agent_id))?;
        self.do_get(&url).await
    }

    /// Pauses an agent.
    pub async fn pause(&self, agent_id: &str) -> Result<Agent, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}/pause", agent_id))?;
        self.do_post(&url, &json!({})).await
    }

    /// Resumes a paused agent.
    pub async fn resume(&self, agent_id: &str) -> Result<Agent, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}/resume", agent_id))?;
        self.do_post(&url, &json!({})).await
    }

    /// Lists tools available to an agent.
    pub async fn list_tools(&self, agent_id: &str) -> Result<Vec<String>, LiuHaoError> {
        let url = self.build_url(&format!("agents/{}/tools", agent_id))?;
        self.do_get(&url).await
    }

    // ---- Internal helpers ----

    fn build_url(&self, path: &str) -> Result<url::Url, LiuHaoError> {
        self.config
            .base_url
            .join(path)
            .map_err(|e| LiuHaoError::Config(format!("Invalid path: {}", e)))
    }

    async fn do_get<T: serde::de::DeserializeOwned>(
        &self,
        url: &url::Url,
    ) -> Result<T, LiuHaoError> {
        let response = self
            .http
            .get(url.clone())
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .send()
            .await
            .map_err(LiuHaoError::from)?;

        self.handle_response(response).await
    }

    async fn do_post<T: serde::de::DeserializeOwned, B: serde::Serialize>(
        &self,
        url: &url::Url,
        body: &B,
    ) -> Result<T, LiuHaoError> {
        let response = self
            .http
            .post(url.clone())
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .json(body)
            .send()
            .await
            .map_err(LiuHaoError::from)?;

        self.handle_response(response).await
    }

    async fn do_put<T: serde::de::DeserializeOwned, B: serde::Serialize>(
        &self,
        url: &url::Url,
        body: &B,
    ) -> Result<T, LiuHaoError> {
        let response = self
            .http
            .put(url.clone())
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .json(body)
            .send()
            .await
            .map_err(LiuHaoError::from)?;

        self.handle_response(response).await
    }

    async fn do_patch<T: serde::de::DeserializeOwned, B: serde::Serialize>(
        &self,
        url: &url::Url,
        body: &B,
    ) -> Result<T, LiuHaoError> {
        let response = self
            .http
            .patch(url.clone())
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .json(body)
            .send()
            .await
            .map_err(LiuHaoError::from)?;

        self.handle_response(response).await
    }

    async fn do_delete<T: serde::de::DeserializeOwned>(
        &self,
        url: &url::Url,
    ) -> Result<T, LiuHaoError> {
        let response = self
            .http
            .delete(url.clone())
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .send()
            .await
            .map_err(LiuHaoError::from)?;

        self.handle_response(response).await
    }

    async fn handle_response<T: serde::de::DeserializeOwned>(
        &self,
        response: reqwest::Response,
    ) -> Result<T, LiuHaoError> {
        let status = response.status();
        if status.is_success() {
            let body = response.text().await.map_err(LiuHaoError::from)?;
            if body.is_empty() {
                return serde_json::from_str("null").map_err(LiuHaoError::from);
            }
            serde_json::from_str(&body).map_err(LiuHaoError::from)
        } else {
            let body: Value = response.json().await.unwrap_or(Value::Null);
            let message = body
                .get("message")
                .and_then(|m| m.as_str())
                .or_else(|| body.get("error").and_then(|e| e.as_str()))
                .unwrap_or("Unknown error")
                .to_string();

            let request_id = None;

            Err(match status.as_u16() {
                400 => {
                    let field_errors = body
                        .get("details")
                        .and_then(|v| v.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default();
                    LiuHaoError::Validation {
                        message,
                        field_errors,
                    }
                }
                401 => LiuHaoError::Authentication(message),
                404 => LiuHaoError::NotFound(message),
                429 => LiuHaoError::RateLimit {
                    message,
                    retry_after_seconds: 0,
                },
                code if code >= 500 => LiuHaoError::API {
                    status: code,
                    message,
                    request_id,
                },
                code => LiuHaoError::API {
                    status: code,
                    message,
                    request_id,
                },
            })
        }
    }
}

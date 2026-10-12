use crate::client::LiuHaoConfig;
use crate::error::LiuHaoError;
use crate::types::{MemoryItem, SearchMemoryRequest, StoreMemoryRequest};
use reqwest::Client;
use serde_json::{json, Value};
use std::sync::Arc;

/// Client for the Memory API.
#[derive(Debug, Clone)]
pub struct MemoryClient {
    config: Arc<LiuHaoConfig>,
    http: Client,
}

impl MemoryClient {
    pub(crate) fn new(config: Arc<LiuHaoConfig>) -> Self {
        let http = Client::builder()
            .timeout(config.timeout)
            .user_agent(&config.user_agent)
            .build()
            .expect("Failed to build HTTP client");
        Self { config, http }
    }

    /// Stores a new memory item.
    pub async fn store(&self, request: StoreMemoryRequest) -> Result<MemoryItem, LiuHaoError> {
        let url = self.build_url("memory/items")?;
        self.do_post(&url, &request).await
    }

    /// Stores a simple memory with content and type.
    pub async fn store_simple(
        &self,
        content: &str,
        memory_type: &str,
    ) -> Result<MemoryItem, LiuHaoError> {
        let request = StoreMemoryRequest {
            content: content.to_string(),
            memory_type: memory_type.to_string(),
            ..Default::default()
        };
        self.store(request).await
    }

    /// Retrieves a memory item by ID.
    pub async fn retrieve(&self, memory_id: &str) -> Result<MemoryItem, LiuHaoError> {
        let url = self.build_url(&format!("memory/items/{}", memory_id))?;
        self.do_get(&url).await
    }

    /// Updates a memory item.
    pub async fn update(
        &self,
        memory_id: &str,
        item: &MemoryItem,
    ) -> Result<MemoryItem, LiuHaoError> {
        let url = self.build_url(&format!("memory/items/{}", memory_id))?;
        self.do_put(&url, item).await
    }

    /// Partially updates a memory item.
    pub async fn patch(
        &self,
        memory_id: &str,
        updates: Value,
    ) -> Result<MemoryItem, LiuHaoError> {
        let url = self.build_url(&format!("memory/items/{}", memory_id))?;
        self.do_patch(&url, &updates).await
    }

    /// Deletes a memory item.
    pub async fn delete(&self, memory_id: &str) -> Result<(), LiuHaoError> {
        let url = self.build_url(&format!("memory/items/{}", memory_id))?;
        self.do_delete::<Value>(&url).await?;
        Ok(())
    }

    /// Searches memory by query.
    pub async fn search(&self, query: &str) -> Result<Vec<MemoryItem>, LiuHaoError> {
        let request = SearchMemoryRequest {
            query: query.to_string(),
            ..Default::default()
        };
        self.search_with_options(request).await
    }

    /// Searches memory with full options.
    pub async fn search_with_options(
        &self,
        request: SearchMemoryRequest,
    ) -> Result<Vec<MemoryItem>, LiuHaoError> {
        let url = self.build_url("memory/search")?;
        self.do_post(&url, &request).await
    }

    /// Lists memory items.
    pub async fn list(&self) -> Result<Vec<MemoryItem>, LiuHaoError> {
        let url = self.build_url("memory/items")?;
        self.do_get(&url).await
    }

    /// Lists memory items with filters.
    pub async fn list_with_options(
        &self,
        memory_type: Option<&str>,
        category: Option<&str>,
        limit: Option<u32>,
        cursor: Option<&str>,
    ) -> Result<Vec<MemoryItem>, LiuHaoError> {
        let mut url = self.build_url("memory/items")?;
        {
            let mut query = url.query_pairs_mut();
            if let Some(mt) = memory_type {
                query.append_pair("memory_type", mt);
            }
            if let Some(c) = category {
                query.append_pair("category", c);
            }
            if let Some(l) = limit {
                query.append_pair("limit", &l.to_string());
            }
            if let Some(c) = cursor {
                query.append_pair("cursor", c);
            }
        }
        self.do_get(&url).await
    }

    /// Clears all memory.
    pub async fn clear(&self) -> Result<(), LiuHaoError> {
        let url = self.build_url("memory/items")?;
        self.do_delete::<Value>(&url).await?;
        Ok(())
    }

    /// Clears memory by type.
    pub async fn clear_by_type(&self, memory_type: &str) -> Result<(), LiuHaoError> {
        let url = self.build_url("memory/clear")?;
        let body = json!({ "memory_type": memory_type });
        self.do_post::<Value, _>(&url, &body).await?;
        Ok(())
    }

    /// Gets all memory categories.
    pub async fn categories(&self) -> Result<Vec<String>, LiuHaoError> {
        let url = self.build_url("memory/categories")?;
        let result: Value = self.do_get(&url).await?;
        Ok(result
            .get("categories")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default())
    }

    /// Gets memory statistics.
    pub async fn stats(&self) -> Result<Value, LiuHaoError> {
        let url = self.build_url("memory/stats")?;
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
            Err(match status.as_u16() {
                400 => LiuHaoError::Validation {
                    message,
                    field_errors: vec![],
                },
                401 => LiuHaoError::Authentication(message),
                404 => LiuHaoError::NotFound(message),
                429 => LiuHaoError::RateLimit {
                    message,
                    retry_after_seconds: 0,
                },
                code => LiuHaoError::API {
                    status: code,
                    message,
                    request_id: None,
                },
            })
        }
    }
}

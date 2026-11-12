use crate::client::LiuHaoConfig;
use crate::error::LiuHaoError;
use crate::types::{
    CreateKnowledgeBaseRequest, KnowledgeBase, KnowledgeSearchResult, SearchKnowledgeRequest,
};
use reqwest::Client;
use serde_json::{json, Value};
use std::sync::Arc;

/// Client for the Knowledge API.
#[derive(Debug, Clone)]
pub struct KnowledgeClient {
    config: Arc<LiuHaoConfig>,
    http: Client,
}

impl KnowledgeClient {
    pub(crate) fn new(config: Arc<LiuHaoConfig>) -> Self {
        let http = Client::builder()
            .timeout(config.timeout)
            .user_agent(&config.user_agent)
            .build()
            .expect("Failed to build HTTP client");
        Self { config, http }
    }

    /// Lists all knowledge bases.
    pub async fn list_bases(&self) -> Result<Vec<KnowledgeBase>, LiuHaoError> {
        let url = self.build_url("knowledge/bases")?;
        self.do_get(&url).await
    }

    /// Lists knowledge bases with pagination.
    pub async fn list_bases_with_options(
        &self,
        limit: Option<u32>,
        cursor: Option<&str>,
    ) -> Result<Vec<KnowledgeBase>, LiuHaoError> {
        let mut url = self.build_url("knowledge/bases")?;
        {
            let mut query = url.query_pairs_mut();
            if let Some(l) = limit {
                query.append_pair("limit", &l.to_string());
            }
            if let Some(c) = cursor {
                query.append_pair("cursor", c);
            }
        }
        self.do_get(&url).await
    }

    /// Creates a new knowledge base.
    pub async fn create_base(
        &self,
        request: CreateKnowledgeBaseRequest,
    ) -> Result<KnowledgeBase, LiuHaoError> {
        let url = self.build_url("knowledge/bases")?;
        self.do_post(&url, &request).await
    }

    /// Creates a knowledge base with just name and description.
    pub async fn create_base_simple(
        &self,
        name: &str,
        description: &str,
    ) -> Result<KnowledgeBase, LiuHaoError> {
        let request = CreateKnowledgeBaseRequest {
            name: name.to_string(),
            description: Some(description.to_string()),
            ..Default::default()
        };
        self.create_base(request).await
    }

    /// Gets a knowledge base by ID.
    pub async fn get_base(&self, base_id: &str) -> Result<KnowledgeBase, LiuHaoError> {
        let url = self.build_url(&format!("knowledge/bases/{}", base_id))?;
        self.do_get(&url).await
    }

    /// Updates a knowledge base.
    pub async fn update_base(
        &self,
        base_id: &str,
        updates: Value,
    ) -> Result<KnowledgeBase, LiuHaoError> {
        let url = self.build_url(&format!("knowledge/bases/{}", base_id))?;
        self.do_patch(&url, &updates).await
    }

    /// Deletes a knowledge base.
    pub async fn delete_base(&self, base_id: &str) -> Result<(), LiuHaoError> {
        let url = self.build_url(&format!("knowledge/bases/{}", base_id))?;
        self.do_delete::<Value>(&url).await?;
        Ok(())
    }

    /// Uploads a document to a knowledge base.
    pub async fn upload_document(
        &self,
        base_id: &str,
        title: &str,
        content: &str,
    ) -> Result<Value, LiuHaoError> {
        let url = self.build_url(&format!("knowledge/bases/{}/documents", base_id))?;
        let body = json!({
            "title": title,
            "content": content,
        });
        self.do_post(&url, &body).await
    }

    /// Uploads a document with metadata.
    pub async fn upload_document_with_metadata(
        &self,
        base_id: &str,
        title: &str,
        content: &str,
        source: Option<&str>,
        metadata: Option<Value>,
    ) -> Result<Value, LiuHaoError> {
        let url = self.build_url(&format!("knowledge/bases/{}/documents", base_id))?;
        let mut body = json!({
            "title": title,
            "content": content,
        });
        if let Some(s) = source {
            body["source"] = Value::String(s.to_string());
        }
        if let Some(m) = metadata {
            body["metadata"] = m;
        }
        self.do_post(&url, &body).await
    }

    /// Searches within a knowledge base.
    pub async fn search(
        &self,
        base_id: &str,
        query: &str,
    ) -> Result<Vec<KnowledgeSearchResult>, LiuHaoError> {
        let request = SearchKnowledgeRequest {
            query: query.to_string(),
            ..Default::default()
        };
        self.search_with_options(base_id, request).await
    }

    /// Searches with full options.
    pub async fn search_with_options(
        &self,
        base_id: &str,
        request: SearchKnowledgeRequest,
    ) -> Result<Vec<KnowledgeSearchResult>, LiuHaoError> {
        let url = self.build_url(&format!("knowledge/bases/{}/search", base_id))?;
        self.do_post(&url, &request).await
    }

    /// Lists documents in a knowledge base.
    pub async fn list_documents(&self, base_id: &str) -> Result<Vec<Value>, LiuHaoError> {
        let url = self.build_url(&format!("knowledge/bases/{}/documents", base_id))?;
        let result: Value = self.do_get(&url).await?;
        Ok(result
            .get("documents")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default())
    }

    /// Lists documents with pagination.
    pub async fn list_documents_with_options(
        &self,
        base_id: &str,
        limit: Option<u32>,
        cursor: Option<&str>,
    ) -> Result<Vec<Value>, LiuHaoError> {
        let mut url = self.build_url(&format!("knowledge/bases/{}/documents", base_id))?;
        {
            let mut query = url.query_pairs_mut();
            if let Some(l) = limit {
                query.append_pair("limit", &l.to_string());
            }
            if let Some(c) = cursor {
                query.append_pair("cursor", c);
            }
        }
        let result: Value = self.do_get(&url).await?;
        Ok(result
            .get("documents")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default())
    }

    /// Deletes a document from a knowledge base.
    pub async fn delete_document(&self, base_id: &str, document_id: &str) -> Result<(), LiuHaoError> {
        let url = self.build_url(&format!(
            "knowledge/bases/{}/documents/{}",
            base_id, document_id
        ))?;
        self.do_delete::<Value>(&url).await?;
        Ok(())
    }

    /// Gets statistics for a knowledge base.
    pub async fn base_stats(&self, base_id: &str) -> Result<Value, LiuHaoError> {
        let url = self.build_url(&format!("knowledge/bases/{}/stats", base_id))?;
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

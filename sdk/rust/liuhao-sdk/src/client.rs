use crate::error::LiuHaoError;
use crate::{agents::AgentsClient, knowledge::KnowledgeClient, memory::MemoryClient};
use reqwest::{Client, Method, RequestBuilder, Response};
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::sync::Arc;
use std::time::Duration;
use url::Url;

const DEFAULT_BASE_URL: &str = "https://api.liuhao.ai/v1";
const DEFAULT_TIMEOUT_SECS: u64 = 30;
const DEFAULT_MAX_RETRIES: u32 = 3;
const DEFAULT_INITIAL_RETRY_DELAY_MS: u64 = 1000;
const DEFAULT_RETRY_BACKOFF_MULTIPLIER: f64 = 2.0;
const DEFAULT_USER_AGENT: &str = "LiuHao-Rust-SDK/0.1.0";

/// Configuration for the LiuHao client.
#[derive(Debug, Clone)]
pub struct LiuHaoConfig {
    pub base_url: Url,
    pub api_key: String,
    pub timeout: Duration,
    pub max_retries: u32,
    pub initial_retry_delay: Duration,
    pub retry_backoff_multiplier: f64,
    pub user_agent: String,
}

impl LiuHaoConfig {
    /// Creates a new configuration builder.
    pub fn builder() -> LiuHaoConfigBuilder {
        LiuHaoConfigBuilder::default()
    }
}

/// Builder for `LiuHaoConfig`.
#[derive(Debug, Clone)]
pub struct LiuHaoConfigBuilder {
    base_url: Option<String>,
    api_key: Option<String>,
    timeout: Option<Duration>,
    max_retries: Option<u32>,
    initial_retry_delay: Option<Duration>,
    retry_backoff_multiplier: Option<f64>,
    user_agent: Option<String>,
}

impl Default for LiuHaoConfigBuilder {
    fn default() -> Self {
        Self {
            base_url: Some(DEFAULT_BASE_URL.to_string()),
            api_key: None,
            timeout: Some(Duration::from_secs(DEFAULT_TIMEOUT_SECS)),
            max_retries: Some(DEFAULT_MAX_RETRIES),
            initial_retry_delay: Some(Duration::from_millis(DEFAULT_INITIAL_RETRY_DELAY_MS)),
            retry_backoff_multiplier: Some(DEFAULT_RETRY_BACKOFF_MULTIPLIER),
            user_agent: Some(DEFAULT_USER_AGENT.to_string()),
        }
    }
}

impl LiuHaoConfigBuilder {
    pub fn base_url(mut self, base_url: impl Into<String>) -> Self {
        self.base_url = Some(base_url.into());
        self
    }

    pub fn api_key(mut self, api_key: impl Into<String>) -> Self {
        self.api_key = Some(api_key.into());
        self
    }

    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.timeout = Some(timeout);
        self
    }

    pub fn max_retries(mut self, max_retries: u32) -> Self {
        self.max_retries = Some(max_retries);
        self
    }

    pub fn initial_retry_delay(mut self, delay: Duration) -> Self {
        self.initial_retry_delay = Some(delay);
        self
    }

    pub fn retry_backoff_multiplier(mut self, multiplier: f64) -> Self {
        self.retry_backoff_multiplier = Some(multiplier);
        self
    }

    pub fn user_agent(mut self, user_agent: impl Into<String>) -> Self {
        self.user_agent = Some(user_agent.into());
        self
    }

    pub fn build(self) -> Result<LiuHaoConfig, LiuHaoError> {
        let api_key = self
            .api_key
            .ok_or_else(|| LiuHaoError::Config("api_key is required".to_string()))?;
        let base_url = Url::parse(
            &self
                .base_url
                .unwrap_or_else(|| DEFAULT_BASE_URL.to_string()),
        )?;

        Ok(LiuHaoConfig {
            base_url,
            api_key,
            timeout: self.timeout.unwrap_or_else(|| Duration::from_secs(DEFAULT_TIMEOUT_SECS)),
            max_retries: self.max_retries.unwrap_or(DEFAULT_MAX_RETRIES),
            initial_retry_delay: self
                .initial_retry_delay
                .unwrap_or_else(|| Duration::from_millis(DEFAULT_INITIAL_RETRY_DELAY_MS)),
            retry_backoff_multiplier: self
                .retry_backoff_multiplier
                .unwrap_or(DEFAULT_RETRY_BACKOFF_MULTIPLIER),
            user_agent: self
                .user_agent
                .unwrap_or_else(|| DEFAULT_USER_AGENT.to_string()),
        })
    }
}

/// The main LiuHao AI OS client.
#[derive(Debug, Clone)]
pub struct LiuHaoClient {
    config: Arc<LiuHaoConfig>,
    http: Client,
    agents: AgentsClient,
    memory: MemoryClient,
    knowledge: KnowledgeClient,
}

impl LiuHaoClient {
    /// Creates a new client builder.
    pub fn builder() -> LiuHaoClientBuilder {
        LiuHaoClientBuilder {
            config_builder: LiuHaoConfig::builder(),
        }
    }

    /// Creates a new client from a config.
    pub fn new(config: LiuHaoConfig) -> Result<Self, LiuHaoError> {
        let http = Client::builder()
            .timeout(config.timeout)
            .user_agent(&config.user_agent)
            .build()
            .map_err(|e| LiuHaoError::Config(format!("Failed to build HTTP client: {}", e)))?;

        let config = Arc::new(config);
        let agents = AgentsClient::new(config.clone());
        let memory = MemoryClient::new(config.clone());
        let knowledge = KnowledgeClient::new(config.clone());

        Ok(Self {
            config,
            http,
            agents,
            memory,
            knowledge,
        })
    }

    /// Access the agents API.
    pub fn agents(&self) -> &AgentsClient {
        &self.agents
    }

    /// Access the memory API.
    pub fn memory(&self) -> &MemoryClient {
        &self.memory
    }

    /// Access the knowledge API.
    pub fn knowledge(&self) -> &KnowledgeClient {
        &self.knowledge
    }

    /// Returns a reference to the configuration.
    pub fn config(&self) -> &LiuHaoConfig {
        &self.config
    }

    // ---- Internal request methods ----

    pub(crate) async fn get<T: DeserializeOwned>(&self, path: &str) -> Result<T, LiuHaoError> {
        self.request(Method::GET, path, None::<()>).await
    }

    pub(crate) async fn post<T: DeserializeOwned, B: serde::Serialize>(
        &self,
        path: &str,
        body: Option<B>,
    ) -> Result<T, LiuHaoError> {
        self.request(Method::POST, path, body).await
    }

    pub(crate) async fn put<T: DeserializeOwned, B: serde::Serialize>(
        &self,
        path: &str,
        body: B,
    ) -> Result<T, LiuHaoError> {
        self.request(Method::PUT, path, Some(body)).await
    }

    pub(crate) async fn patch<T: DeserializeOwned, B: serde::Serialize>(
        &self,
        path: &str,
        body: B,
    ) -> Result<T, LiuHaoError> {
        self.request(Method::PATCH, path, Some(body)).await
    }

    pub(crate) async fn delete<T: DeserializeOwned>(&self, path: &str) -> Result<T, LiuHaoError> {
        self.request(Method::DELETE, path, None::<()>).await
    }

    pub(crate) async fn delete_with_body<T: DeserializeOwned, B: serde::Serialize>(
        &self,
        path: &str,
        body: B,
    ) -> Result<T, LiuHaoError> {
        self.request(Method::DELETE, path, Some(body)).await
    }

    async fn request<T: DeserializeOwned, B: serde::Serialize>(
        &self,
        method: Method,
        path: &str,
        body: Option<B>,
    ) -> Result<T, LiuHaoError> {
        let url = self.build_url(path)?;
        let mut attempts = 0;
        let mut delay = self.config.initial_retry_delay;
        let mut last_error: Option<LiuHaoError> = None;

        loop {
            let mut request = self
                .http
                .request(method.clone(), url.clone())
                .header("Authorization", format!("Bearer {}", self.config.api_key))
                .header("Accept", "application/json");

            if let Some(ref b) = body {
                request = request.json(b);
            }

            match self.execute_request(request).await {
                Ok(response) => {
                    let status = response.status();
                    let request_id = response
                        .headers()
                        .get("X-Request-ID")
                        .and_then(|v| v.to_str().ok())
                        .map(|s| s.to_string());

                    if status.is_success() {
                        let body_text = response.text().await.map_err(LiuHaoError::from)?;
                        if body_text.is_empty() {
                            return serde_json::from_str("null").map_err(LiuHaoError::from);
                        }
                        return serde_json::from_str(&body_text).map_err(LiuHaoError::from);
                    }

                    let err = self.build_error(status, response, request_id).await;
                    if err.is_retryable() && attempts < self.config.max_retries {
                        tracing::warn!(
                            "Request failed (attempt {}): {}",
                            attempts + 1,
                            err
                        );
                        last_error = Some(err);
                    } else {
                        return Err(err);
                    }
                }
                Err(e) => {
                    if e.is_retryable() && attempts < self.config.max_retries {
                        tracing::warn!(
                            "Request failed (attempt {}): {}",
                            attempts + 1,
                            e
                        );
                        last_error = Some(e);
                    } else {
                        return Err(e);
                    }
                }
            }

            attempts += 1;
            if attempts > self.config.max_retries {
                break;
            }

            tokio::time::sleep(delay).await;
            delay =
                Duration::from_millis((delay.as_millis() as f64 * self.config.retry_backoff_multiplier) as u64);
        }

        Err(last_error.unwrap_or_else(|| {
            LiuHaoError::Other("Max retries exceeded".to_string())
        }))
    }

    async fn execute_request(
        &self,
        request: RequestBuilder,
    ) -> Result<Response, LiuHaoError> {
        request.send().await.map_err(LiuHaoError::from)
    }

    async fn build_error(
        &self,
        status: reqwest::StatusCode,
        response: Response,
        request_id: Option<String>,
    ) -> LiuHaoError {
        let body: Value = response
            .json()
            .await
            .unwrap_or_else(|_| Value::Null);

        let message = body
            .get("message")
            .and_then(|m| m.as_str())
            .or_else(|| body.get("error").and_then(|e| e.as_str()))
            .unwrap_or("Unknown error")
            .to_string();

        match status.as_u16() {
            400 => {
                let field_errors = body
                    .get("details")
                    .or_else(|| body.get("errors"))
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
            429 => {
                let retry_after = body
                    .get("retry_after")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0) as u32;
                LiuHaoError::RateLimit {
                    message,
                    retry_after_seconds: retry_after,
                }
            }
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
        }
    }

    fn build_url(&self, path: &str) -> Result<Url, LiuHaoError> {
        self.config
            .base_url
            .join(path)
            .map_err(|e| LiuHaoError::Config(format!("Invalid path '{}': {}", path, e)))
    }
}

/// Builder for `LiuHaoClient`.
pub struct LiuHaoClientBuilder {
    config_builder: LiuHaoConfigBuilder,
}

impl LiuHaoClientBuilder {
    pub fn api_key(mut self, api_key: impl Into<String>) -> Self {
        self.config_builder = self.config_builder.api_key(api_key);
        self
    }

    pub fn base_url(mut self, base_url: impl Into<String>) -> Self {
        self.config_builder = self.config_builder.base_url(base_url);
        self
    }

    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.config_builder = self.config_builder.timeout(timeout);
        self
    }

    pub fn max_retries(mut self, max_retries: u32) -> Self {
        self.config_builder = self.config_builder.max_retries(max_retries);
        self
    }

    pub fn config(mut self, config: LiuHaoConfig) -> Self {
        self.config_builder = LiuHaoConfigBuilder {
            base_url: Some(config.base_url.to_string()),
            api_key: Some(config.api_key),
            timeout: Some(config.timeout),
            max_retries: Some(config.max_retries),
            initial_retry_delay: Some(config.initial_retry_delay),
            retry_backoff_multiplier: Some(config.retry_backoff_multiplier),
            user_agent: Some(config.user_agent),
        };
        self
    }

    pub fn build(self) -> Result<LiuHaoClient, LiuHaoError> {
        let config = self.config_builder.build()?;
        LiuHaoClient::new(config)
    }
}

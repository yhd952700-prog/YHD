use thiserror::Error;

/// Errors that can occur when using the LiuHao SDK.
#[derive(Debug, Error)]
pub enum LiuHaoError {
    /// Authentication failed (HTTP 401).
    #[error("Authentication failed: {0}")]
    Authentication(String),

    /// The requested resource was not found (HTTP 404).
    #[error("Not found: {0}")]
    NotFound(String),

    /// Request validation failed (HTTP 400).
    #[error("Validation error: {message}")]
    Validation {
        message: String,
        field_errors: Vec<String>,
    },

    /// Rate limit exceeded (HTTP 429).
    #[error("Rate limit exceeded, retry after {retry_after_seconds}s")]
    RateLimit {
        message: String,
        retry_after_seconds: u32,
    },

    /// Server-side API error (HTTP 5xx).
    #[error("API error ({status}): {message}")]
    API {
        status: u16,
        message: String,
        request_id: Option<String>,
    },

    /// Request timed out.
    #[error("Request timed out")]
    Timeout,

    /// Network or transport error.
    #[error("Network error: {0}")]
    Network(String),

    /// Error parsing the response.
    #[error("Parse error: {0}")]
    Parse(String),

    /// Invalid configuration.
    #[error("Configuration error: {0}")]
    Config(String),

    /// Other unexpected errors.
    #[error("{0}")]
    Other(String),
}

impl From<reqwest::Error> for LiuHaoError {
    fn from(err: reqwest::Error) -> Self {
        if err.is_timeout() {
            LiuHaoError::Timeout
        } else if err.is_status() {
            let status = err.status().unwrap();
            LiuHaoError::API {
                status: status.as_u16(),
                message: err.to_string(),
                request_id: None,
            }
        } else {
            LiuHaoError::Network(err.to_string())
        }
    }
}

impl From<serde_json::Error> for LiuHaoError {
    fn from(err: serde_json::Error) -> Self {
        LiuHaoError::Parse(err.to_string())
    }
}

impl From<url::ParseError> for LiuHaoError {
    fn from(err: url::ParseError) -> Self {
        LiuHaoError::Config(format!("Invalid URL: {}", err))
    }
}

impl LiuHaoError {
    /// Returns true if the error is retryable.
    pub fn is_retryable(&self) -> bool {
        matches!(
            self,
            LiuHaoError::Timeout
                | LiuHaoError::Network(_)
                | LiuHaoError::RateLimit { .. }
                | LiuHaoError::API { status: 502..=504, .. }
        )
    }

    /// Returns the HTTP status code if available.
    pub fn status_code(&self) -> Option<u16> {
        match self {
            LiuHaoError::Authentication(_) => Some(401),
            LiuHaoError::NotFound(_) => Some(404),
            LiuHaoError::Validation { .. } => Some(400),
            LiuHaoError::RateLimit { .. } => Some(429),
            LiuHaoError::API { status, .. } => Some(*status),
            _ => None,
        }
    }
}

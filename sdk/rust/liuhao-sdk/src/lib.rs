//! Official LiuHao AI OS SDK for Rust.
//!
//! This crate provides a fully async Rust client for the LiuHao AI OS API,
//! supporting agents, memory, knowledge bases, tasks, and health checks.
//!
//! # Quick Start
//!
//! ```no_run
//! use liuhao_sdk::LiuHaoClient;
//!
//! # #[tokio::main]
//! # async fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let client = LiuHaoClient::builder()
//!     .api_key("your-api-key")
//!     .build()?;
//!
//! let agents = client.agents().list().await?;
//! println!("Found {} agents", agents.len());
//! # Ok(())
//! # }
//! ```

pub mod agents;
pub mod client;
pub mod error;
pub mod knowledge;
pub mod memory;
pub mod types;

pub use client::{LiuHaoClient, LiuHaoConfig};
pub use error::LiuHaoError;
pub use types::*;

package com.liuhao.sdk.models;

import com.google.gson.annotations.SerializedName;

/**
 * Status of an AI Agent.
 */
public enum AgentStatus {
    @SerializedName("idle")
    IDLE,

    @SerializedName("running")
    RUNNING,

    @SerializedName("paused")
    PAUSED,

    @SerializedName("stopped")
    STOPPED,

    @SerializedName("error")
    ERROR,

    @SerializedName("training")
    TRAINING,

    @SerializedName("deploying")
    DEPLOYING
}

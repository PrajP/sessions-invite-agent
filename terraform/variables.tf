variable "project_id" {
  description = "The Google Cloud Project ID to deploy resources into."
  type        = string
  default     = "demo-payment-reconciliation"
}

variable "region" {
  description = "The Google Cloud region for compute, secret manager, and firestore."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "The name of the Cloud Run and Agent service."
  type        = string
  default     = "payment-reconciliation-agent"
}

variable "image_uri" {
  description = "Container image URI in Artifact Registry for the agent deployment."
  type        = string
  default     = "us-central1-docker.pkg.dev/demo-payment-reconciliation/agents/payment-reconciliation-agent:v1"
}

variable "firestore_database_name" {
  description = "The Firestore database name for persistent agent session storage."
  type        = string
  default     = "(default)"
}

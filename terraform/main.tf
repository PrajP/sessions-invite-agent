terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.25.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ---------------------------------------------------------------------------
# 1. Enable Required Google Cloud APIs
# ---------------------------------------------------------------------------
resource "google_project_service" "enabled_apis" {
  for_each = toset([
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "firestore.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudtrace.googleapis.com"
  ])
  service            = each.key
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# 2. Artifact Registry for Agent Containers
# ---------------------------------------------------------------------------
resource "google_artifact_registry_repository" "agent_repo" {
  depends_on    = [google_project_service.enabled_apis]
  location      = var.region
  repository_id = "agents"
  description   = "Docker repository for ADK Payment Reconciliation Agent"
  format        = "DOCKER"
}

# ---------------------------------------------------------------------------
# 3. Google Cloud Secret Manager for Zero-Hardcoded Secrets
# ---------------------------------------------------------------------------
resource "google_secret_manager_secret" "gemini_api_key" {
  depends_on = [google_project_service.enabled_apis]
  secret_id  = "GEMINI_API_KEY"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "hitl_hmac_secret" {
  depends_on = [google_project_service.enabled_apis]
  secret_id  = "HITL_HMAC_SECRET"

  replication {
    auto {}
  }
}

# ---------------------------------------------------------------------------
# 4. Google Cloud Firestore Database (Native Mode) for Session Memory
# ---------------------------------------------------------------------------
resource "google_firestore_database" "database" {
  depends_on                  = [google_project_service.enabled_apis]
  name                        = var.firestore_database_name
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"
  deletion_policy             = "DELETE_PROTECTION_PREVENT"
}

# ---------------------------------------------------------------------------
# 5. Dedicated Least-Privilege Service Account
# ---------------------------------------------------------------------------
resource "google_service_account" "agent_sa" {
  account_id   = "payment-agent-sa"
  display_name = "Payment Reconciliation Agent Runner Service Account"
}

resource "google_project_iam_member" "sa_secret_access" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "sa_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "sa_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "sa_trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

# ---------------------------------------------------------------------------
# 6. Cloud Run v2 Service (Cost-Effective Serverless Hosting)
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "agent_service" {
  depends_on = [
    google_project_service.enabled_apis,
    google_firestore_database.database
  ]
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.agent_sa.email

    scaling {
      min_instance_count = 0 # Cost-effective: scales to zero when idle
      max_instance_count = 5
    }

    containers {
      image = var.image_uri

      resources {
        limits = {
          cpu    = "1000m"
          memory = "1024Mi"
        }
      }

      env {
        name  = "ENV"
        value = "production"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "USE_SECRET_MANAGER"
        value = "true"
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = var.firestore_database_name
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "HITL_HMAC_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.hitl_hmac_secret.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

# Allow public invocations for webhook ingress and 1-click mobile approvals
resource "google_cloud_run_service_iam_member" "public_invoker" {
  location = google_cloud_run_v2_service.agent_service.location
  project  = var.project_id
  service  = google_cloud_run_v2_service.agent_service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "service_url" {
  description = "The public HTTPS URL of the deployed payment agent service."
  value       = google_cloud_run_v2_service.agent_service.uri
}

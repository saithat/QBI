# Infrastructure

Infrastructure definitions and deployment notes live here. Root `Dockerfile` and `compose.yaml`
remain at their historic paths for compatibility. `kubernetes/` contains the credential-free
Kustomize base, kind overlay, local lifecycle automation, and bounded runtime smoke Job. Terraform
and production-managed dependency provisioning remain deferred.

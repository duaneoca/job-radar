# Kubernetes Deployment

JobRadar deploys to a k3s cluster on an AWS EC2 instance.

## One-time Setup

### 1. Launch an EC2 instance (AWS Free Tier)

- AMI: Ubuntu 22.04 LTS
- Instance type: `t2.micro` (free tier) or `t3.small` for more headroom
- Open ports: 22 (SSH), 80 (HTTP), 443 (HTTPS), 6443 (kubectl)

### 2. Install k3s

```bash
curl -sfL https://get.k3s.io | sh -
```

k3s includes the NGINX ingress controller and a local storage provisioner out of the box.

### 3. Get your kubeconfig

```bash
sudo cat /etc/rancher/k3s/k3s.yaml
```

Replace `127.0.0.1` with your EC2 public IP. Base64-encode this and store it as
`KUBECONFIG_STAGING` and `KUBECONFIG_PRODUCTION` GitHub Actions secrets.

```bash
cat k3s.yaml | base64
```

### 4. Create namespaces

```bash
kubectl create namespace jobradar-staging
kubectl create namespace jobradar-production
```

### 5. Create secrets

```bash
# Staging
kubectl create secret generic tracker-api-secrets \
  --from-literal=DATABASE_URL='your-neon-db-url' \
  --from-literal=SECRET_KEY='$(python3 -c "import secrets; print(secrets.token_hex(32))")' \
  -n jobradar-staging

# Production (same command, different namespace)
kubectl create secret generic tracker-api-secrets \
  --from-literal=DATABASE_URL='your-neon-db-url' \
  --from-literal=SECRET_KEY='$(python3 -c "import secrets; print(secrets.token_hex(32))")' \
  -n jobradar-production
```

## Deploying

Deployments are handled automatically by GitHub Actions. But you can also deploy manually:

```bash
# Staging
kubectl apply -k k8s/overlays/staging

# Production
kubectl apply -k k8s/overlays/production
```

## Useful Commands

```bash
# Check running pods
kubectl get pods -n jobradar-staging

# View tracker-api logs
kubectl logs -l app=tracker-api -n jobradar-staging --follow

# Check ingress
kubectl get ingress -n jobradar-staging

# Restart a deployment
kubectl rollout restart deployment/jobradar-tracker-api -n jobradar-staging
```

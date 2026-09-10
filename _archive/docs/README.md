# Moose Sports Empire

The ultimate fantasy baseball companion platform that transforms your league experience with AI-powered insights, real-time matchup analysis, and automated recaps. Stop juggling spreadsheets and start dominating your league with intelligent tools that do the heavy lifting for you.

---

This guide walks you through deploying the Moose Sports Empire platform to your VPS using **Traefik** for reverse proxy, SSL certificates, and routing. Perfect for users who already have Traefik running!

> **What You'll Deploy**: A full-stack fantasy baseball platform with AI-powered recaps, real-time matchup analysis, and comprehensive league management.

---

## Prerequisites

### VPS Requirements
- Linux VPS (Ubuntu 22.04+ recommended)
- **Docker** and **Docker Compose** installed
- **Traefik** already running and configured
- At least 2GB RAM, 1 CPU core
- 20GB+ storage space

### External Services
- **Yahoo Developer App** (for OAuth login)
- **Google Gemini API Key** (for AI recaps)
- Optional: OpenRouter API Key (LLM fallback)
- Optional: The Odds API Key (multiplier data)

---

## Quick Deployment (15 minutes)

### Step 1: Clone and Setup
```bash
# Clone the repository
git clone https://github.com/jarettrude/fantasy-baseball-league-sabermetrics.git
cd fantasy-baseball-league-sabermetrics

# Create secrets directory
mkdir -p secrets
```

### Step 2: Choose Your Setup

#### Production Deployment (Recommended for VPS)
Use the production docker-compose file:

```bash
# The production docker-compose.yml is already set up
# Edit the file with your domain and settings
nano docker-compose.yml
```

#### Development Setup (Local Only)
If you just want to test locally first:

```bash
# Use the development docker-compose file
docker compose -f docker-compose.dev.yml up -d --build
```

### Step 3: Setup Local Traefik Certificates (Development Only)

For local development, you need self-signed certificates in `infra/traefik/certs/`:

```bash
# Create certs directory
mkdir -p infra/traefik/certs

# Generate self-signed certificate for localhost
openssl req -x509 -newkey rsa:4096 -keyout infra/traefik/certs/local-key.pem \
  -out infra/traefik/certs/local-cert.pem -days 365 -nodes \
  -subj "/C=US/ST=State/L=City/O=Development/CN=localhost"
```

**Note**: Your browser will warn about self-signed certificates - this is normal for development. Click "Advanced" and "Proceed to localhost" to continue.

### Step 4: Configure Your Domain (Production Only)
Edit your production `docker-compose.yml` and update these environment variables:

```yaml
environment:
  WEB_ORIGIN: https://your-domain.com          # Your main domain
  PUBLIC_API_URL: https://api.your-domain.com  # Your API subdomain
  YAHOO_REDIRECT_URI: https://your-domain.com/callback
  LOCAL_TIMEZONE: America/New_York            # Your timezone
```

Also update the Traefik labels:
```yaml
labels:
  - "traefik.http.routers.moose-api.rule=Host(`api.your-domain.com`)"
  - "traefik.http.routers.moose-web.rule=Host(`your-domain.com`) || Host(`www.your-domain.com`)"
  - "traefik.http.routers.moose-api.tls.certresolver=letsencrypt"
  - "traefik.http.routers.moose-web.tls.certresolver=letsencrypt"
```

### Step 5: Setup Secrets

#### Development Setup (Local)
Create the following files in `secrets/` with `.txt` extensions:

```bash
# Yahoo OAuth (get from https://developer.yahoo.com/apps/)
echo "your_yahoo_client_id" > secrets/yahoo_client_id.txt
echo "your_yahoo_client_secret" > secrets/yahoo_client_secret.txt
echo "your_yahoo_league_id" > secrets/yahoo_league_id.txt

# Security Keys (generate these!)
openssl rand -base64 48 | tr -d '\n' > secrets/jwt_secret_key.txt
openssl rand -base64 48 | tr -d '\n' > secrets/session_secret.txt
openssl rand -base64 48 | tr -d '\n' > secrets/csrf_secret.txt

# Fernet Key for database encryption
uv run --project apps/api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > secrets/fernet_key.txt

# API Keys
echo "your_google_gemini_api_key" > secrets/google_gemini_api_key.txt
echo "your_openrouter_api_key" > secrets/openrouter_api_key.txt
echo "your_the_odds_api_key" > secrets/the_odds_api_key.txt

# Commissioner Yahoo GUID (gatekeeping - must be set before deployment)
echo "your_commissioner_yahoo_guid" > secrets/commissioner_yahoo_guid.txt
```

> **Note**: Development uses `.txt` extensions while production uses extensionless files. This prevents accidentally using development secrets in production without intentionally renaming the files.

#### Production Setup (VPS)
Create extensionless files in `${DOCKER_DIR}/secrets/moose_sports_empire/`:

```bash
# Create production secrets directory
mkdir -p ${DOCKER_DIR}/secrets/moose_sports_empire

# Yahoo OAuth
echo "your_production_yahoo_client_id" > ${DOCKER_DIR}/secrets/moose_sports_empire/yahoo_client_id
echo "your_production_yahoo_client_secret" > ${DOCKER_DIR}/secrets/moose_sports_empire/yahoo_client_secret
echo "your_production_yahoo_league_id" > ${DOCKER_DIR}/secrets/moose_sports_empire/yahoo_league_id

# Security Keys (generate fresh production keys)
openssl rand -base64 48 | tr -d '\n' > ${DOCKER_DIR}/secrets/moose_sports_empire/jwt_secret_key
openssl rand -base64 48 | tr -d '\n' > ${DOCKER_DIR}/secrets/moose_sports_empire/session_secret
openssl rand -base64 48 | tr -d '\n' > ${DOCKER_DIR}/secrets/moose_sports_empire/csrf_secret

# Fernet Key for database encryption
uv run --project /path/to/moose_sports_empire/apps/api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > ${DOCKER_DIR}/secrets/moose_sports_empire/fernet_key

# API Keys
echo "your_production_google_gemini_api_key" > ${DOCKER_DIR}/secrets/moose_sports_empire/google_gemini_api_key
echo "your_production_openrouter_api_key" > ${DOCKER_DIR}/secrets/moose_sports_empire/openrouter_api_key
echo "your_production_the_odds_api_key" > ${DOCKER_DIR}/secrets/moose_sports_empire/the_odds_api_key

# Commissioner Yahoo GUID (gatekeeping - must be set before deployment)
echo "your_commissioner_yahoo_guid" > ${DOCKER_DIR}/secrets/moose_sports_empire/commissioner_yahoo_guid

# Production database password (secure!)
echo "secure_db_password" > ${DOCKER_DIR}/secrets/moose_sports_empire/db_password
```

### Step 5.5: Create Production Environment File

Create a `.env` file in the root directory of your moose_sports_empire clone:

```bash
# Create .env file for docker-compose
nano .env
```

Add the following content (adjust paths for your setup):

```bash
# Docker directory paths
DOCKER_DIR=/path/to/your/docker/setup
```

**Why this is needed**: The production `docker-compose.yml` uses `${DOCKER_DIR}` variables for volume paths and secret locations. Creating this `.env` file ensures docker-compose can automatically resolve these paths without requiring manual exports each time you run commands.

**Example setup**:
```bash
# If your docker setup is at /opt/docker
DOCKER_DIR=/opt/docker

# The full paths will resolve to:
# /opt/docker/appdata/moose_sports_empire/postgres
# /opt/docker/secrets/moose_sports_empire/yahoo_client_id
# etc.
```

### Step 6: Deploy!

#### Production Deployment:
```bash
# Build and start all services (.env file provides DOCKER_DIR)
docker compose up -d --build

# Watch the logs to see migrations run
docker compose logs -f api
```

#### Development Deployment:
```bash
# Build and start all services using dev compose file
docker compose -f docker-compose.dev.yml up -d --build

# Watch the logs to see migrations run
docker compose -f docker-compose.dev.yml logs -f api

# Access at https://localhost (Traefik handles HTTPS)
```

That's it! Your app should be live at your domain (production) or localhost (development).

---

## Detailed Configuration

### Production vs Development Differences

| Feature | Development | Production |
|---------|-------------|------------|
| **Docker Compose** | `docker-compose.dev.yml` | `docker-compose.yml` |
| **Dockerfile** | `Dockerfile.dev` | `Dockerfile` |
| **Database** | Simple credentials (moose/moose) | Secure password via secrets |
| **Network** | Traefik HTTPS (localhost) | Traefik reverse proxy |
| **Volumes** | Hot reload mounts | Persistent data only |
| **SSL** | Traefik self-signed HTTPS | Traefik auto-HTTPS |
| **Secrets** | Local files | Production secret paths |

### Traefik Router Configuration (Production Only)

Add these routes to your Traefik dynamic configuration:

```yaml
# traefik/dynamic/moose-sports.yml
http:
  routers:
    moose-web:
      rule: "Host(`your-domain.com`) || Host(`www.your-domain.com`)"
      service: "moose-web"
      entryPoints: ["websecure"]
      tls:
        certResolver: "letsencrypt"

    moose-api:
      rule: "Host(`api.your-domain.com`)"
      service: "moose-api"
      entryPoints: ["websecure"]
      tls:
        certResolver: "letsencrypt"

  services:
    moose-web:
      loadBalancer:
        servers:
          - url: "http://localhost:4321"  # Web container port

    moose-api:
      loadBalancer:
        servers:
          - url: "http://localhost:8000"  # API container port
```

### Environment Variables Reference

#### Development (.env or docker-compose.dev.yml)
```yaml
WEB_ORIGIN: https://localhost
PUBLIC_API_URL: https://localhost
YAHOO_REDIRECT_URI: https://localhost/callback
```

**Note**: Development uses Traefik with HTTPS for Yahoo OAuth compatibility

#### Production (docker-compose.yml)
```yaml
WEB_ORIGIN: https://your-domain.com
PUBLIC_API_URL: https://api.your-domain.com
YAHOO_REDIRECT_URI: https://your-domain.com/callback
```

---

## Service Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Traefik       │────│   Web Frontend  │────│   Browser       │
│   (Reverse      │    │   (Astro/Svelte)│    │                 │
│    Proxy)       │    │   Port: 4321    │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │
         │                       │
         ▼                       ▼
┌─────────────────┐    ┌─────────────────┐
│   API Backend   │────│   Redis Cache   │
│   (FastAPI)     │    │   Port: 6379    │
│   Port: 8000    │    └─────────────────┘
└─────────────────┘             │
         │                      │
         ▼                      ▼
┌─────────────────┐    ┌─────────────────┐
│  PostgreSQL     │    │   Background    │
│  Database       │    │   Worker        │
│  Port: 5432     │    │   (AI Recaps)   │
└─────────────────┘    └─────────────────┘
```

---

## Management Commands

### Daily Operations
```bash
# Check service status
docker compose ps

# View logs
docker compose logs -f --tail 50

# Restart services
docker compose restart

# Update the application
git pull && docker compose up -d --build
```

### Database Management
```bash
# Backup database (production - adjust container name)
docker exec moose-sports-db pg_dump -U moose moose_empire > backup.sql

# Restore database
docker exec -i moose-sports-db psql -U moose moose_empire < backup.sql

# Access database directly
docker exec -it moose-sports-db psql -U moose moose_empire
```

### Monitoring
```bash
# Resource usage
docker compose stats

# Disk usage
docker system df

# Clean up old images
docker system prune -f
```

---

## Security Best Practices

### Already Implemented
- **Docker Secrets**: All sensitive data in `/run/secrets/`
- **Auto-HTTPS**: Traefik handles SSL certificates
- **Database Encryption**: Fernet key for sensitive data
- **CSRF Protection**: Built-in middleware
- **JWT Tokens**: Secure session management

---

## Troubleshooting

### Common Issues

#### 401 Unauthorized Errors
```bash
# Check WEB_ORIGIN matches Yahoo app settings
grep WEB_ORIGIN docker-compose.yml
# Must exactly match your Yahoo Developer App redirect URI
```

#### Database Connection Issues
```bash
# Check database health
docker compose exec db pg_isready -U moose

# Reset database (WARNING: deletes data)
docker compose down -v
docker compose up -d
```

#### API Not Responding
```bash
# Check API logs for errors
docker compose logs api

# Restart just the API
docker compose restart api
```

#### Frontend Not Loading
```bash
# Check API URL is correct
grep PUBLIC_API_URL docker-compose.yml

# Rebuild frontend
docker compose up -d --build web
```

### Development vs Production Issues

#### Development: Hot Reload Not Working
```bash
# Check volume mounts are present
docker compose config

# Verify volume mounts in docker-compose.yml
grep -A 10 volumes: docker-compose.yml
```

#### Production: Traefik Not Routing
```bash
# Check Traefik dashboard for router status
# Verify container names match labels
docker compose ps

# Check network connectivity
docker network ls
docker network inspect proxy
```

### Get Help
- **Check logs**: `docker compose logs -f service-name`
- **Verify configuration**: Compare with examples above
- **Network issues**: Ensure Traefik can reach container ports
- **Development issues**: Check override file is loaded

---

## Scaling & Performance

### Resource Recommendations
- **Small League (1-10 teams)**: 2GB RAM, 1 CPU core
- **Medium League (11-20 teams)**: 4GB RAM, 2 CPU cores  
- **Large League (20+ teams)**: 8GB RAM, 4 CPU cores

### Optimization Tips
```yaml
# Add to production docker-compose.yml
services:
  api:
    deploy:
      resources:
        limits:
          memory: 1G
        reservations:
          memory: 512M
  
  worker:
    deploy:
      resources:
        limits:
          memory: 512M
```

---

## Updates & Maintenance

### Monthly Updates
```bash
# Update application
git pull origin main
docker compose pull
docker compose up -d --build

# Update system packages
sudo apt update && sudo apt upgrade -y
```

### Season Setup
```bash
# Update league ID for new season
echo "new_league_id" > secrets/yahoo_league_id.txt

# Reset commissioner GUID (will be updated after first login)
echo "" > secrets/commissioner_yahoo_guid.txt

# Restart services
docker compose restart api worker
```

---

## You're All Set!

Your Moose Sports Empire is now running with:
- **Automatic SSL** via Traefik (production only)
- **Database migrations** handled automatically  
- **AI-powered recaps** scheduled daily
- **Secure authentication** via Yahoo OAuth
- **Real-time updates** and live scoring

**Next Steps**:
1. Log in at your domain (production) or https://localhost (development) with Yahoo
2. Verify your league data loads correctly
3. Check that AI recaps generate overnight
4. Set up monitoring/alerts as needed

---

## Support

If you run into issues:
1. Check the troubleshooting section above
2. Review logs: `docker compose logs -f`
3. Verify your Traefik configuration (production only)
4. Ensure all secrets are properly configured

Enjoy your fantasy baseball platform!

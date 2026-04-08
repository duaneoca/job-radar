# JobRadar 🎯

An AI-powered job search and tracking platform. JobRadar scrapes job boards, reviews listings against your personal criteria using an LLM, tracks your application pipeline, and monitors your inbox for employer responses.

## Architecture

JobRadar is a Python microservices application deployed on Kubernetes.

| Service | Description |
|---|---|
| `tracker-api` | FastAPI REST backend — the central hub for all job data |
| `scraper` | Playwright-based worker scraping LinkedIn, Indeed, Glassdoor, Dice |
| `ai-reviewer` | LLM-powered job scoring and summarization (Claude API) |
| `email-monitor` | Gmail API integration — watches inbox for employer responses |
| `frontend` | React dashboard — Kanban-style job pipeline view |

## Tech Stack

- **Language:** Python 3.12 / Node.js (frontend)
- **API:** FastAPI + SQLAlchemy + Alembic
- **Database:** PostgreSQL (Neon in production)
- **Queue:** Redis + Celery
- **Scraping:** Playwright
- **AI:** Claude API (Anthropic)
- **Auth:** OAuth2 (Gmail)
- **Containers:** Docker
- **Orchestration:** Kubernetes (k3s)
- **Cloud:** AWS EC2
- **CI/CD:** GitHub Actions
- **Registry:** GitHub Container Registry (GHCR)

## Getting Started (Local Development)

### Prerequisites

- Docker & Docker Compose
- Python 3.12+
- Node.js 20+

### 1. Clone the repo

```bash
git clone git@github.com:duaneoca/job-radar.git
cd job-radar
```

### 2. Set up environment files

Each service has a `.env.example`. Copy and fill in your values:

```bash
cp services/tracker-api/.env.example services/tracker-api/.env
cp services/scraper/.env.example services/scraper/.env
cp services/ai-reviewer/.env.example services/ai-reviewer/.env
cp services/email-monitor/.env.example services/email-monitor/.env
```

### 3. Start the stack

```bash
docker compose up --build
```

The tracker API will be available at `http://localhost:8000`.
API docs at `http://localhost:8000/docs`.

## Project Structure

```
job-radar/
├── services/
│   ├── tracker-api/        # FastAPI backend
│   ├── scraper/            # Job board scraper
│   ├── ai-reviewer/        # AI job scoring
│   ├── email-monitor/      # Gmail integration
│   └── frontend/           # React dashboard
├── k8s/
│   ├── base/               # Kubernetes manifests
│   └── overlays/           # staging / production (Kustomize)
├── .github/
│   └── workflows/          # CI/CD pipelines
└── docker-compose.yml      # Local dev
```

## CI/CD

Three GitHub Actions workflows:

| Workflow | Trigger | Action |
|---|---|---|
| `pr.yml` | Pull request | Lint + test + build check |
| `deploy-staging.yml` | Push to `main` | Build → push to GHCR → deploy staging |
| `deploy-production.yml` | Git tag `v*.*.*` | Promote to production |

## Deployment

See [`k8s/README.md`](k8s/README.md) for Kubernetes deployment instructions.

## License

[MIT](LICENSE)

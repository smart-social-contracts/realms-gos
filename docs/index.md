---
layout: home

hero:
  name: realms
  tagline: Welcome to the documentation of realms Governance Operating System. Build and deploy transparent, efficient governance systems on the Internet Computer.
  image:
    src: /img/logo_sphere_only.svg
    alt: Realms
  actions:
    - theme: brand
      text: Get Started
      link: /guide/getting-started
    - theme: alt
      text: View on GitHub
      link: https://github.com/smart-social-contracts/realms-gos
    - theme: alt
      text: Try Demo
      link: https://demo.realmsgos.org

features:
  - icon: 🌐
    title: Internet Computer Native
    details: Deploy fully on-chain governance systems with seamless integration on the Internet Computer blockchain.
  
  - icon: 🔌
    title: Extension System
    details: Powerful modular architecture allowing custom extensions for voting, treasury, identity, and more.
  
  - icon: 🔒
    title: Secure & Transparent
    details: Built-in security with transparent governance rules enforced by smart contracts.
  
  - icon: 💻
    title: Developer Friendly
    details: Python/Basilisk backend with SvelteKit frontend. Simple CLI for rapid development and deployment.
  
  - icon: 🏛️
    title: Multi-Realm Support
    details: Deploy multiple interconnected governance systems with shared registry via Mundus.
  
  - icon: ⚡
    title: Instant Setup
    details: Get a full governance system running with a single command. Demo data included.
---

## Quick Start

Install the Realms CLI and create your first governance system:

```bash
# Install the CLI
pip install realms-gos

# Create a realm with demo data
realms realm create --random --citizens 50 --deploy

# Access your realm
# Frontend: http://<canister_id>.localhost:8000
```

## What is Realms?

Realms is a decentralized software platform for public administration — a Governance Operating System (GOS) designed to deliver essential public services such as justice, social welfare, property registries, and more.

From small towns to entire nations, it empowers communities to design, run, and evolve their own governance systems — free from the corruption and inefficiencies of traditional bureaucracy.

## Key Features

- **Fully Auditable**: Every action is recorded on-chain
- **AI-Powered**: Intelligent automation for governance tasks
- **Extensible**: Build custom extensions for your specific needs
- **Transparent**: All governance rules are visible and verifiable
- **Efficient**: Eliminate bureaucratic overhead and corruption

## Learn More

- [Non-Technical Introduction](/reference/NON_TECHNICAL_INTRO) - Understand what Realms is
- [Technical Overview](/reference/TECHNICAL_INTRO) - Architecture and development
- [Operations](/OPERATIONS) - Deploy realms (`AGENTS.md` in repo root)
- [CLI Reference](/reference/CLI_REFERENCE) - Command-line tools

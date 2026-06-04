# Alex Rivera

Seattle, WA · alex.rivera@example.com · linkedin.com/in/alex-rivera-demo · github.com/alex-rivera-demo

## Summary

Senior backend engineer with 5+ years of Python experience building high-throughput distributed systems. Track record of operating revenue-critical payment services on-call, partnering with risk and finance teams, and driving incremental migrations from legacy monoliths to focused services.

## Experience

### Senior Software Engineer, Northwind Cloud
*Aug 2021 – Present · Seattle, WA*

- Designed and shipped Python services on the payments team, including invoice-retry logic that recovered ~$2.4M/yr in transient declines.
- Cut p95 latency on the nightly billing batch from 88m to 14m by parallelizing PostgreSQL reads and rewriting the hot loop in Go.
- Mentored junior engineers and reviewed their code.
- Wrote internal documentation for the team.
- Carried primary on-call for the billing service; reduced page volume ~30% by replacing raw error-rate alerts with SLO-based alerting in Datadog.

### Software Engineer, BlueOrbit Systems
*Jun 2019 – Jul 2021 · Remote*

- Built and operated the rate-limited public API in Python (used by 200+ partner integrations), including JWT auth, request shaping, and Kafka-backed audit logging.
- Led the cut-over of the user-billing module out of a legacy Django monolith into a focused Python service, with a feature-flagged dual-write phase to de-risk the migration.
- Set up CI/CD pipelines using GitHub Actions.

### Software Engineering Intern, Quarry Labs
*May 2018 – Aug 2018 · Boston, MA*

- Wrote a Slack bot for tracking on-call rotations.

## Education

### B.S. in Computer Science, State University of New York
*2015 – 2019*

## Skills

Python, Go, PostgreSQL, Kafka, Kubernetes, AWS (EC2, S3, RDS, Lambda, SQS), Terraform, Docker, Datadog, Linux, Git.

## Projects

### scratchpad-db

A small key-value store I wrote in Go for fun. Implements a write-ahead log, basic compaction, and an HTTP API.

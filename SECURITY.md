# Security policy

KingoAssistant interacts with an academic system and may process account-session material. Treat all credentials and personal academic data as sensitive.

## Do not publish

Do not create public issues, pull requests, screenshots, logs, or test fixtures containing:

- passwords, cookies, session IDs, bearer tokens, signatures, or API keys;
- raw HAR files or unredacted request/response bodies;
- student IDs, names, class rosters, grades, schedules, or other personal academic records.

Use synthetic data in tests. Redact secrets before sharing diagnostic output.

## Responsible use

This project is intended for personally authorized, officially permitted access. It must not be used to bypass CAPTCHA, authentication, access controls, selection-window restrictions, or server-side rate limits, or to generate high-frequency request bursts against school infrastructure.

The current Windows client remains read-only. The automatic-enrollment state machine is an offline-tested orchestration core; no NCWU live write adapter is implemented or enabled. A transport acknowledgement or a displayed seat count is not proof of successful enrollment.

## Reporting

For a suspected vulnerability, use GitHub's **Report a vulnerability** option if private vulnerability reporting is enabled for this repository. Do not disclose exploitable details or credentials in a public issue. If private reporting is unavailable, contact the repository owner privately through GitHub.

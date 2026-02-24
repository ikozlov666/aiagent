# Browser Automation — Production Readiness Checklist

This checklist focuses on stabilizing UI automation flows that run through `browser_*` tools.

## 1) Reliability defaults

- Prefer stable selectors (`data-testid`, `name`, semantic roles) over CSS based on layout.
- Use explicit waits (`browser_wait`) before actions on dynamic pages.
- Pass per-action `timeout` for slow environments:
  - `browser_navigate({"url": "...", "timeout": 45000})`
  - `browser_click({"selector": "...", "timeout": 8000})`
  - `browser_type({"selector": "...", "text": "...", "timeout": 8000})`

## 2) Incident diagnostics

- On browser action failures, the backend now automatically captures a full-page debug screenshot into `.screenshots/` and returns `debug_screenshot_path` in the tool result.
- Persist tool outputs in your job/event store so failed runs retain:
  - `error`
  - `url`
  - `title`
  - `debug_screenshot_path`

## 3) Scenario design

- Keep flows small and composable (login, create item, publish, logout).
- Add an idempotent setup step for test data.
- Add one smoke flow per critical user journey and run it after each deployment.

## 4) Environment and execution model

- Keep browser automation inside sandbox containers only.
- Enforce upper bounds for run duration (job timeout + max steps).
- Run a canary target first (staging URL), then production.

## 5) Observability and SLO

Track at least:

- Success rate per scenario.
- Median and P95 step duration.
- Failure categories: selector not found, timeout, network 4xx/5xx.
- Number of retries per run.

Suggested initial SLO:

- Smoke suite pass rate: **>= 98%** on staging over rolling 7 days.

## 6) Security

- Never log secrets from forms.
- Redact credentials from tool args/results before persistence.
- Restrict outbound domains if possible (allowlist your app domains).

## 7) Rollout plan

1. Stabilize 3 core smoke scenarios.
2. Enable nightly full suite.
3. Add deploy gate on smoke suite (staging first).
4. Enable read-only production checks.

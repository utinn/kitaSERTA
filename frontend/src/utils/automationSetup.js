// Eligibility and validation for the MVP automation setup.
import { asArray, normalizeStep } from "./candidateFormat.js";

// MVP supports a single automation: GitHub PR merged → Slack notification.
const SUPPORTED_TRIGGER = "GITHUB_PULL_REQUEST_MERGED";
const SLACK_PATTERN = /slack/i;

// GitHub "owner/name"; Slack public (C…) or private (G…) channel IDs.
const REPOSITORY_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;
const CHANNEL_ID_PATTERN = /^[CG][A-Z0-9]{8,}$/;

export function isSupportedAutomation(proposal) {
  if (proposal.trigger !== SUPPORTED_TRIGGER) return false;
  return asArray(proposal.steps)
    .map(normalizeStep)
    .some((step) => SLACK_PATTERN.test(`${step.tool ?? ""} ${step.action ?? ""}`));
}

export function validateSetup({ repository, channelId }) {
  const errors = {};
  if (!REPOSITORY_PATTERN.test(repository.trim())) {
    errors.repository = "Enter the repository as owner/name, for example acme/web-app.";
  }
  if (!CHANNEL_ID_PATTERN.test(channelId.trim())) {
    errors.channelId = "Enter a Slack Channel ID such as C0123456789 (not the #channel name).";
  }
  return errors;
}

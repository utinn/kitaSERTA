import { asArray, normalizeStep } from "./candidateFormat.js";

const SUPPORTED_TRIGGER = "GITHUB_PULL_REQUEST_MERGED";
const ALLOWED_TOOLS = new Set(["github", "slack"]);
const SLACK_WORD = /\bslack\b/i;
const OTHER_SERVICES =
  /\b(jira|trello|asana|linear|notion|teams|discord|email|e-mail|gmail|outlook|sms|whatsapp|telegram|webhook|zapier|confluence|clickup|monday)\b/i;

const REPOSITORY_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;
const CHANNEL_ID_PATTERN = /^[CG][A-Z0-9]{8,}$/;

function normalizeTrigger(trigger) {
  return typeof trigger === "string"
    ? trigger.trim().toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "")
    : "";
}

export function approvedDestination(proposal) {
  const destination = proposal?.resolution?.destination;
  if (
    destination?.source !== "automation_config" ||
    !REPOSITORY_PATTERN.test(destination.allowed_repository ?? "") ||
    !CHANNEL_ID_PATTERN.test(destination.slack_channel_id ?? "")
  ) {
    return null;
  }
  return {
    repository: destination.allowed_repository,
    channelId: destination.slack_channel_id,
  };
}

export function isSupportedAutomation(proposal, title = "") {
  if (normalizeTrigger(proposal.trigger) !== SUPPORTED_TRIGGER) return false;

  const steps = asArray(proposal.steps).map(normalizeStep);
  if (steps.length === 0) return false;

  const tools = new Set(
    [...asArray(proposal.required_tools), ...steps.map((step) => step.tool)]
      .filter((tool) => typeof tool === "string" && tool.trim())
      .map((tool) => tool.trim().toLowerCase())
  );
  if ([...tools].some((tool) => !ALLOWED_TOOLS.has(tool))) return false;

  const texts = [
    ...steps.map((step) => step.action).filter((text) => typeof text === "string"),
    ...asArray(proposal.expected_side_effects).filter((text) => typeof text === "string"),
  ];
  if (!tools.has("slack") && !texts.some((text) => SLACK_WORD.test(text))) return false;
  if ([...texts, typeof title === "string" ? title : ""].some((text) => OTHER_SERVICES.test(text))) {
    return false;
  }

  if ("resolution" in proposal) {
    const resolution = proposal.resolution;
    return (
      resolution?.kind === "destination_resolution" &&
      resolution.action === "SLACK_SEND_MESSAGE" &&
      approvedDestination(proposal) !== null
    );
  }
  return true;
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
